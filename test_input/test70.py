'test checking constant conditions'

# __pychecker__ = ''

def func1(x):
    'should not produce a warning'
    if 1:
        pass
    while 1:
        print x
        break
    assert x, 'test'
    return 0

def func2(x):
    'should produce a warning'
    __pychecker__ = 'constant1'
    if 1:
        pass
    while 1:
        print x
        break
    return 0

def func3(x):
    'should produce a warning'
    if 21:
        return 1
    if 31:
        return 2
    assert(x, 'test')
    assert(5, 'test')
    assert 5, 'test'

    if 'str':
        return 3

    return 4
