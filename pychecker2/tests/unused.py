
def f(a):
    return 1

def g():
    class Test:
        def q(self):
            class TestInner:
                def p(self, a):
                    return a
            self.foo = TestInner()
            
                    
x, y = 1, 2
class Object: pass
x = Object()
x.y, x.z = 1, 2

def h(a, b, c):
    assert 0, "should not be called"

