
"doc string"

def x(a, b, c = None, d = None, **kw):
    pass

def y():
    x(1, 2, c=3, d=4, e=5)

class X:
    "doc"
    def __init__(self):
        self.y = 0
    def z(self):
        "this should not have any warnings"
        x(self.y, { 'a': 'b' })

