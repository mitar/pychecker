#!/usr/bin/env python

# Copyright (c) 2001, MetaSlash Inc.  All rights reserved.

"""
Check python source code files for possible errors and print warnings
"""

import string
import types
import sys
import imp
import os
import glob

import printer
import warn
import OP
import Config

_DEFAULT_MODULE_TOKENS = [ '__builtins__', '__doc__', '__file__', '__name__', ]
_DEFAULT_CLASS_TOKENS = [ '__doc__', '__name__', '__module__', ]

_ARGS_ARGS_FLAG = 4
_KW_ARGS_FLAG = 8


# Globals for storing a dictionary of info about modules and classes
_allModules = {}
_cfg = None

def _flattenList(list) :
    "Returns a list which contains no lists"

    new_list = []
    for element in list :
        if type(element) == types.ListType :
            return new_list + _flattenList(element)
        new_list.append(element)
    
    return new_list

def getModules(arg_list) :
    "Returns a list of module names that can be imported"

    new_arguments = []
    for arg in arg_list :
        # is this a wildcard filespec? (necessary for windows)
        if '*' in arg or '?' in arg or '[' in arg :
            arg = glob.glob(arg)
        new_arguments.append(arg)

    PY_PREFIX = '.py'
    PY_PREFIX_LEN = len(PY_PREFIX)

    modules = []
    for arg in _flattenList(new_arguments) :
        # is it a .py file?
        if len(arg) > PY_PREFIX_LEN and arg[-PY_PREFIX_LEN:] == PY_PREFIX:
            arg_dir = os.path.dirname(arg)
            module_name = os.path.basename(arg)[:-PY_PREFIX_LEN]
            if arg_dir not in sys.path :
                sys.path.append(arg_dir)
	    arg = module_name
        modules.append(arg)

    return _flattenList(modules)


def findModule(name, path = sys.path) :
    """Returns the result of an imp.find_module(), ie, (file, filename, smt)
       name can be a module or a package name.  It is *not* a filename."""

    packages = string.split(name, '.')
    for p in packages :
        # smt = (suffix, mode, type)
        file, filename, smt = imp.find_module(p, path)
        if smt[-1] == imp.PKG_DIRECTORY :
            # package found - read path info from init file
            m = imp.load_module(p, file, filename, smt)
            path = m.__path__
        else:
            if p is not packages[-1] :
                raise ImportError, "No module named %s" % packages[-1]
            return file, filename, smt

    raise ImportError, "No module named %s" % packages[-1]


class Variable :
    "Class to hold all information about a variable"

    def __init__(self, name, type):
        self.name = name
        self.type = type


class Function :
    "Class to hold all information about a function"

    def __init__(self, function, isMethod = None) :
        self.function = function
        self.maxArgs = function.func_code.co_argcount
        if isMethod :
            self.maxArgs = self.maxArgs - 1
        self.minArgs = self.maxArgs
        if function.func_defaults != None :
            self.minArgs = self.minArgs - len(function.func_defaults)
        # if function uses *args, there is no max # args
        if function.func_code.co_flags & _ARGS_ARGS_FLAG != 0 :
            self.maxArgs = None
        self.supportsKW = function.func_code.co_flags & _KW_ARGS_FLAG


def _filterDir(object, ignoreList) :
    "Return a list of tokens (attributes) in a class, except for ignoreList"

    tokens = dir(object)
    for token in ignoreList :
        if token in tokens :
            tokens.remove(token)
    return tokens

def _getClassTokens(c) :
    return _filterDir(c, _DEFAULT_CLASS_TOKENS)


class Class :
    "Class to hold all information about a class"

    def __init__(self, name, module) :
        self.name = name
        self.module = module
        self.classObject = getattr(module, name)
        self.methods = {}
        self.members = { '__class__': types.ClassType,
                         '__doc__': types.StringType,
                         '__dict__': types.DictType, }

    def getFirstLine(self) :
        "Return first line we can find in THIS class, not any base classes"

        lineNums = []
        classDir = dir(self.classObject)
        for m in self.methods.values() :
            if m != None and m.function.func_code.co_name in classDir:
                lineNums.append(m.function.func_code.co_firstlineno)
        if lineNums :
            return min(lineNums)
        return 0


    def allBaseClasses(self, c = None) :
        "Return a list of all base classes for this class and it's subclasses"

        baseClasses = []
        if c == None :
            c = self.classObject
        for base in c.__bases__ :
            baseClasses = baseClasses + [ base ] + self.allBaseClasses(base)
        return baseClasses

    def __getMethodName(self, func_name, className = None) :
        if func_name[0:2] == '__' and func_name[-2:] != '__' :
            if className == None :
                className = self.name
            if className[0] != '_' :
                className = '_' + className
            func_name = className + func_name
        return func_name

    def addMethod(self, method, className = None) :
        if type(method) == types.StringType :
            self.methods[method] = None
            return
        if not hasattr(method, "func_name") :
            return

        methodName = self.__getMethodName(method.func_name, className)
        self.methods[methodName] = Function(method, 1)

    def addMethods(self, classObject) :
        for classToken in _getClassTokens(classObject) :
            token = getattr(classObject, classToken)
            if type(token) == types.MethodType :
                self.addMethod(token.im_func, classObject.__name__)
            else :
                self.members[classToken] = type(token)

        # add standard methods
        for methodName in [ '__class__', ] :
            self.addMethod(methodName, classObject.__name__)

    def addMembers(self, classObject) :
        if not _cfg.onlyCheckInitForMembers :
            for classToken in _getClassTokens(classObject) :
                method = getattr(classObject, classToken)
                if type(method) == types.MethodType :
                    self.addMembersFromMethod(method.im_func)
        elif hasattr(classObject, "__init__") :
            self.addMembersFromMethod(classObject.__init__.im_func)

    def addMembersFromMethod(self, method) :
        if not hasattr(method, 'func_code') :
            return

        func_code, code, i, maxCode, extended_arg = OP.initFuncCode(method)
        stack = []
        while i < maxCode :
            op, oparg, i, extended_arg = OP.getInfo(code, i, extended_arg)
            if op >= OP.HAVE_ARGUMENT :
                operand = OP.getOperand(op, func_code, oparg)
                if OP.LOAD_CONST(op) or OP.LOAD_FAST(op) :
                    stack.append(operand)
                elif OP.STORE_ATTR(op) :
                    if len(stack) > 0 :
                        if stack[-1] == 'self' :
                            value = None
                            if len(stack) > 1 :
                                value = type(stack[-2])
                            self.members[operand] = value
                        stack = []


def importError(moduleName, info):
    # detail may contain a newline replace with - 
    # use str to avoid undestanding the tuple structure in the exception
    info = string.join(string.split(str(info), '\n' ), ' - ')
    print "  Problem importing module %s - %s" % (moduleName, info)


class Module :
    "Class to hold all information for a module"

    def __init__(self, moduleName) :
    # def __init__(self, filename, moduleName) :
        # self.filename = filename
        self.moduleName = moduleName
        self.variables = {}
        self.functions = {}
        self.classes = {}
        self.modules = {}
        self.module = None
        global _allModules
        _allModules[moduleName] = self

    def addVariable(self, var, varType) :
        self.variables[var] = Variable(var, varType)

    def addFunction(self, func) :
        self.functions[func.__name__] = Function(func)

    def __addAttributes(self, c, classObject) :
        for base in classObject.__bases__ :
            self.__addAttributes(c, base)
        c.addMethods(classObject)
        c.addMembers(classObject)

    def addClass(self, name) :
        self.classes[name] = c = Class(name, self.module)
        self.__addAttributes(c, c.classObject)

    def addModule(self, name) :
        if not _allModules.has_key(name) :
            self.modules[name] = Module(name)

    def filename(self) :
        if not self.module :
            return self.moduleName
        filename = self.module.__file__
        if string.lower(filename[-4:]) == '.pyc' :
            filename = filename[:-4] + '.py'
        return filename

    def load(self) :
        try :
	    file, filename, smt = findModule(self.moduleName)
            self.module = imp.load_module(self.moduleName, file, filename, smt)
        except (ImportError, NameError, SyntaxError), detail:
            # not sure which errors we should check here, maybe all?
            return importError(self.moduleName, detail)

        for tokenName in _filterDir(self.module, _DEFAULT_MODULE_TOKENS) :
            token = getattr(self.module, tokenName)
            tokenType = type(token)
            if tokenType == types.ModuleType :
                self.addModule(tokenName)
            elif tokenType == types.FunctionType :
                self.addFunction(token)
            elif tokenType == types.ClassType :
                self.addClass(tokenName)
            else :
                self.addVariable(tokenName, tokenType)

        return 1


def main(argv) :
    if not '.' in sys.path :
        sys.path.append('.')

    global _cfg
    _cfg, files = Config.setupFromArgs(argv[1:])
    importWarnings = []
    for moduleName in getModules(files) :
        print "Processing %s..." % moduleName
        module = Module(moduleName)
        if not module.load() :
            w = warn.Warning(module.filename(), 1, "NOT PROCESSED UNABLE TO IMPORT")
            importWarnings.append(w)

    if _cfg.printParse :
        for module in _allModules.values() :
            printer.module(module)

    print "\nWarnings...\n"
    warnings = warn.find(_allModules.values(), _cfg)
    if warnings or importWarnings :
        warnings.sort()
        lastWarning = None
        for warning in importWarnings + warnings :
            if lastWarning != None :
                # ignore duplicate warnings
                if cmp(lastWarning, warning) == 0 :
                    continue
                # print blank line between files
                if lastWarning.file != warning.file :
                    print ""

            lastWarning = warning
            warning.output()
        sys.exit(1)
    else :
        print "None"
        sys.exit(0)


if __name__ == '__main__' :
    main(sys.argv)
