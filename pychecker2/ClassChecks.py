from pychecker2.Check import Check
from pychecker2.Check import Warning
from pychecker2 import symbols
from pychecker2.util import BaseVisitor, parents, type_filter, line

from compiler.misc import mangle
from compiler import ast, walk

_ignorable = {}
for ignore in ['repr', 'dict', 'class', 'doc', 'str']:
    _ignorable['__%s__' % ignore] = 1

class GetDefs(BaseVisitor):
    "Record definitions of a attribute of self, who's name is provided"
    def __init__(self, name):
        self.selfname = name
        self.result = {}

    def visitAssAttr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname and \
           isinstance(node.parent, (ast.Assign, ast.AssTuple)):
            self.result[node.attrname] = node

    def visitClass(self, node):         # ignore nested classes
        pass

class GetRefs(BaseVisitor):
    "Record references to a attribute of self, who's name is provided"
    def __init__(self, name):
        self.selfname = name
        self.result = {}

    def visitAssAttr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname and \
           not isinstance(node.parent, (ast.Assign, ast.AssTuple)):
            self.result[node.attrname] = node
        self.visitChildren(node)

    def visitGetattr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname:
            self.result[node.attrname] = node
        self.visitChildren(node)

    def visitClass(self, node):         # ignore nested classes
        pass


def _get_methods(class_scope):
    return type_filter(class_scope.get_children(), symbols.FunctionScope)

class NotSimpleName(Exception): pass

# compress Getattr(Getattr(Name(x), y), z) -> "x.y.z"
def get_name(node):
    if isinstance(node, ast.Getattr):
        return get_name(node.expr) + (node.attrname, )
    elif isinstance(node, ast.Name):
        return (node.name,)
    else:
        raise NotSimpleName

def get_base_names(scope):
    names = []
    for b in scope.node.bases:
        try:
            names.append(get_name(b))
        except NotSimpleName:       # FIXME: hiding expressions
            pass
    return names

def find_in_module(package, remotename, names, checker):
    # No other names, must be a name from the module
    if not names:
        f = checker.check_module(package)
        if f:
            return find_scope_going_down(f.root_scope, [remotename], checker)
        return None

    # complex name lookup
    #  first, get the real name of the package
    name = package.__name__
    if remotename:
        name += "." + remotename
    #  now import it, and chase down any other modules
    module = __import__(name, globals(), {}, [''])
    submodule = getattr(module, names[0], None)
    if type(submodule) == type(symbols):
        return find_in_module(submodule, None, names[1:], checker)
    
    #  object in the module is not another module, so chase down the source
    f = checker.check_module(module)
    if f:
        return find_scope_going_down(f.root_scope, names, checker)
    return None
                 
def find_scope_going_down(scope, names, checker):
    "Drill down scopes to find definition of x.y.z"
    for c in scope.get_children():
        if getattr(c, 'name', '') == names[0]:
            if len(names) == 1:
                return c
            return find_scope_going_down(c, names[1:], checker)
    # Not defined here, check for import
    return find_imported_class(scope.imports, names, checker)

def find_imported_class(imports, names, checker):
    # may be defined by import
    for i in range(1, len(names) + 1):
        # try x, then x.y, then x.y.z as imported names
        try:
            name = ".".join(names[:i])
            ref = imports[name]
            # now look for the rest of the name
            result = find_in_module(ref.module, ref.remotename, names[i:], checker)
            if result:
                return result
        except (KeyError, ImportError):
            pass
    return None

def find_scope_going_up(scope, names, checker):
    "Search up to find scope defining x of x.y.z"
    for p in parents(scope):
        if p.defs.has_key(names[0]):
            return find_scope_going_down(p, names, checker)
    return None

def get_base_classes(scope, checker):
    result = []
    for name in get_base_names(scope):
        base = find_scope_going_up(scope, name, checker)
        if base:
            result.append(base)
            result.extend(get_base_classes(base, checker))
    return result

def conforms(a, b):
    alen = len(a.node.argnames)
    blen = len(b.node.argnames)
    # f(a, *args) conforms to f(a, b, *args)
    if alen != blen:
        if alen < blen:
            return None
        elif not a.node.varargs or not b.node.varargs:
            return None
    return a.node.kwargs == b.node.kwargs and a.node.varargs == b.node.varargs

class AttributeCheck(Check):
    "check `self.attr' expressions for attr"

    unknownAttribute = Warning('Report unknown object attributes in methods',
                           'Class %s has no attribute %s')
    unusedAttribute = Warning('Report attributes unused in methods',
                              'Attribute %s is not used in class %s')
    missingSelf = Warning('Report methods without "self"',
                          'Method %s is missing self parameter')
    methodRedefined = Warning('Report the redefinition of class methods',
                              'Method %s defined at line %d in '
                              'class %s redefined')
    signatureChanged = Warning('Report methods whose signatures do not '
                               'match base class methods',
                               'Signature does not match method '
                               '%s in base class %s')
    attributeInitialized = \
                 Warning('Report attributes not initialized in __init__',
                         'Attribute %s is not initialized in __init__')
                                   
    def check(self, file, checker):
        def visit_with_self(Visitor, method):
            if not method.node.argnames:
                return {}
            return walk(method.node, Visitor(method.node.argnames[0])).result

        # for all class scopes
        for scope in type_filter(file.scopes.values(), symbols.ClassScope):
            bases = get_base_classes(scope, checker)
            # get attributes defined on self
            init_attributes = None
            attributes = {}             # "self.foo = " kinda things
            methods = {}                # methods -> scopes
            inherited = {}              # all class defs (methods)
            inherited_attributes = {}
            
            for m in _get_methods(scope):
                if not m.node.argnames:
                    file.warning(m.node, self.missingSelf, m.node.name)
                defs = visit_with_self(GetDefs, m)
                if m.name == '__init__':
                    init_attributes = defs
                attributes.update(defs)
                methods[m.name] = m

            # complain about attributes not initialized in __init__
            if init_attributes is not None:
                for name, node in attributes.items():
                    if not init_attributes.has_key(name):
                        file.warning(line(node),
                                     self.attributeInitialized, name)

            for base in [scope] + bases:
                for m in _get_methods(base):
                    inherited_attributes.update(visit_with_self(GetDefs, m))
                    if m.name != "__init__" and \
                       methods.has_key(m.name) and \
                       not conforms(m, methods[m.name]):
                        file.warning(methods[m.name].node,
                                     self.signatureChanged,
                                     m.name, base.name)
                    else:
                        methods[m.name] = m
                inherited.update(base.defs)

            # complain about defs with the same name as methods
            for name, node in inherited_attributes.items():
                try:
                    orig = methods[mangle(name, scope.name)]
                    file.warning(line(node), self.methodRedefined,
                                 name, orig.lineno, scope.name)
                    break
                except KeyError:
                    pass

            # find refs on self
            refs = {}
            for m in _get_methods(scope):
                refs.update(visit_with_self(GetRefs, m))

            # Now complain about refs on self that aren't known
            for name, node in refs.items():
                if not inherited_attributes.has_key(name) and \
                   not _ignorable.get(name, None) and \
                   not scope.defs.has_key(mangle(name, scope.name)) and \
                   not inherited.has_key(name):
                    file.warning(line(node), self.unknownAttribute,
                                 scope.name, name)

            for name, node in attributes.items():
                if not refs.has_key(name):
                    if name.startswith('__'):
                        file.warning(line(node), self.unusedAttribute,
                                     name, scope.name)

class GetReturns(BaseVisitor):

    def __init__(self):
        self.result = []

    def visitReturn(self, node):
        self.result.append(node)

    def visitFunction(self, node): pass
    visitClass = visitFunction

class InitCheck(Check):

    initReturnsValue = Warning('Report value returned from __init__',
                               'Method __init__ should not return a value')

    def check(self, file, unused_checker):

        for scope in type_filter(file.scopes.values(), symbols.ClassScope):
            for m in _get_methods(scope):
                if m.name == '__init__':
                    for r in walk(m.node.code, GetReturns()).result:
                        if isinstance(r.value, ast.Const) and \
                           r.value.value is None:
                            continue
                        if isinstance(r.value, ast.Name) and \
                           r.value.name == 'None':
                            continue
                        file.warning(r, self.initReturnsValue)

                            

special = {
    '__cmp__': 2,     '__del__': 1,     '__delitem__': 2, '__eq__': 2,
    '__ge__': 2,      '__getitem__': 2, '__gt__': 2,      '__hash__': 1,
    '__le__': 2,      '__len__': 1,     '__lt__': 2,      '__ne__': 2,
    '__nonzero__': 1, '__repr__': 1,    '__setitem__': 3, '__str__': 1,
    '__getattr__': 2, '__setattr__': 3,
    '__delattr__': 2, '__len__': 1,     '__delitem__': 2, '__iter__': 1,
    '__contains__': 2,'__setslice__': 4,'__delslice__': 3,
    '__add__': 2,     '__sub__': 2,     '__mul__': 2,     '__floordiv__': 2,
    '__mod__': 2,     '__divmod__': 2,  '__lshift__': 2,
    '__rshift__': 2,  '__and__': 2,     '__xor__': 2,     '__or__': 2,
    '__div__': 2,     '__truediv__': 2, '__radd__': 2,    '__rsub__': 2,
    '__rmul__': 2,    '__rdiv__': 2,    '__rmod__': 2,    '__rdivmod__': 2,
    '__rpow__': 2,    '__rlshift__': 2, '__rrshift__': 2, '__rand__': 2,
    '__rxor__': 2,    '__ror__': 2,     '__iadd__': 2,    '__isub__': 2,
    '__imul__': 2,    '__idiv__': 2,    '__imod__': 2,    '__ilshift__': 2,
    '__irshift__': 2, '__iand__': 2,    '__ixor__': 2,    '__ior__': 2,
    '__neg__': 1,     '__pos__': 1,     '__abs__': 1,     '__invert__': 1,
    '__complex__': 1, '__int__': 1,     '__long__': 1,    '__float__': 1,
    '__oct__': 1,     '__hex__': 1,     '__coerce__': 2,
    '__getinitargs__': 1,
    '__getstate__': 1,'__setstate__': 2,
    '__copy__': 1,    '__deepcopy__': 1,
    '__pow__': 2,     '__ipow__': 2,    # 2 or 3
    '__call__': None,                   # any number > 1
    '__getslice__': 3,                  # deprecated
    '__getattribute__': 2,
    }

def check_special(scope):
    try:
        count = special[scope.name]
        max_args = len(scope.node.argnames)
        min_args = max_args - len(scope.node.defaults)
        if min_args > count or max_args < count or \
           scope.node.varargs or scope.node.kwargs:
            return special[scope.name]
    except KeyError:
        pass
    return None

class SpecialCheck(Check):

    specialMethod = Warning('Report special methods with incorrect '
                            'number of arguments',
                            'The %s method requires %d argument%s, '
                            'including self')

    notSpecial = Warning('Report methods with "__" prefix and suffix '
                         'which are not defined as special methods',
                         'The method %s is not a special method, '
                         'but is reserved.')

    def check(self, file, unused_checker):

        for scope in type_filter(file.scopes.values(), symbols.ClassScope):
            for m in _get_methods(scope):
                n = check_special(m)
                if n:
                    file.warning(m.node, self.specialMethod, m.name, n,
                                 n > 1 and "s" or "")
                name = m.name
                if name.startswith('__') and name.endswith('__') and \
                   name != '__init__' and not special.has_key(name):
                    file.warning(m.node, self.notSpecial, name)

class BackQuote(BaseVisitor):

    def __init__(self, selfname):
        self.results = []
        self.selfname = selfname

    def visitBackquote(self, node):
        if isinstance(node.expr, ast.Name) and node.expr.name == self.selfname:
            self.results.append(node)

class ReprCheck(Check):

    backquoteSelf = Warning('Report use of `self` in __repr__ methods',
                           'Using `self` in __repr__')
    def check(self, file, unused_checker):
        for scope in type_filter(file.scopes.values(), symbols.ClassScope):
            for m in _get_methods(scope):
                if m.name == '__repr__' and m.node.argnames:
                    visitor = BackQuote(m.node.argnames[0])
                    for n in walk(m.node.code, visitor).results:
                        file.warning(line(n), self.backquoteSelf)
