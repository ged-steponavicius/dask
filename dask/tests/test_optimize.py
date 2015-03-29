from operator import add, mul
from toolz import partial, identity
from dask.utils import raises
from dask.optimize import (cull, fuse, inline, inline_functions, functions_of,
        dealias, equivalent, sync_vars, merge_sync)


def inc(x):
    return x + 1


def double(x):
    return x * 2


def test_cull():
    # 'out' depends on 'x' and 'y', but not 'z'
    d = {'x': 1, 'y': (inc, 'x'), 'z': (inc, 'x'), 'out': (add, 'y', 10)}
    culled = cull(d, 'out')
    assert culled == {'x': 1, 'y': (inc, 'x'), 'out': (add, 'y', 10)}
    assert cull(d, 'out') == cull(d, ['out'])
    assert cull(d, ['out', 'z']) == d
    assert cull(d, [['out'], ['z']]) == cull(d, ['out', 'z'])
    assert raises(KeyError, lambda: cull(d, 'badkey'))


def test_fuse():
    assert fuse({
        'w': (inc, 'x'),
        'x': (inc, 'y'),
        'y': (inc, 'z'),
        'z': (add, 'a', 'b'),
        'a': 1,
        'b': 2,
    }) == {
        'w': (inc, (inc, (inc, (add, 'a', 'b')))),
        'a': 1,
        'b': 2,
    }
    assert fuse({
        'NEW': (inc, 'y'),
        'w': (inc, 'x'),
        'x': (inc, 'y'),
        'y': (inc, 'z'),
        'z': (add, 'a', 'b'),
        'a': 1,
        'b': 2,
    }) == {
        'NEW': (inc, 'y'),
        'w': (inc, (inc, 'y')),
        'y': (inc, (add, 'a', 'b')),
        'a': 1,
        'b': 2,
    }
    assert fuse({
        'v': (inc, 'y'),
        'u': (inc, 'w'),
        'w': (inc, 'x'),
        'x': (inc, 'y'),
        'y': (inc, 'z'),
        'z': (add, 'a', 'b'),
        'a': (inc, 'c'),
        'b': (inc, 'd'),
        'c': 1,
        'd': 2,
    }) == {
        'u': (inc, (inc, (inc, 'y'))),
        'v': (inc, 'y'),
        'y': (inc, (add, 'a', 'b')),
        'a': (inc, 1),
        'b': (inc, 2),
    }
    assert fuse({
        'a': (inc, 'x'),
        'b': (inc, 'x'),
        'c': (inc, 'x'),
        'd': (inc, 'c'),
        'x': (inc, 'y'),
        'y': 0,
    }) == {
        'a': (inc, 'x'),
        'b': (inc, 'x'),
        'd': (inc, (inc, 'x')),
        'x': (inc, 0),
    }
    assert fuse({
        'a': 1,
        'b': (inc, 'a'),
        'c': (add, 'b', 'b')
    }) == {
        'b': (inc, 1),
        'c': (add, 'b', 'b')
    }

def test_inline():
    d = {'a': 1, 'b': (inc, 'a'), 'c': (inc, 'b'), 'd': (add, 'a', 'c')}
    assert inline(d) == {'b': (inc, 1), 'c': (inc, 'b'), 'd': (add, 1, 'c')}
    assert inline(d, ['a', 'b', 'c']) == {'d': (add, 1, (inc, (inc, 1)))}

    d = {'x': 1, 'y': (inc, 'x'), 'z': (add, 'x', 'y')}
    assert inline(d) == {'y': (inc, 1), 'z': (add, 1, 'y')}
    assert inline(d, keys='y') == {'z': (add, 1, (inc, 1))}
    assert inline(d, keys='y', inline_constants=False) == {
        'x': 1, 'z': (add, 'x', (inc, 'x'))}


def test_inline_functions():
    x, y, i, d = 'xyid'
    dsk = {'out': (add, i, d),
           i: (inc, x),
           d: (double, y),
           x: 1, y: 1}

    result = inline_functions(dsk, fast_functions=set([inc]))
    expected = {'out': (add, (inc, x), d),
                d: (double, y),
                x: 1, y: 1}
    assert result == expected


def test_inline_ignores_curries_and_partials():
    dsk = {'x': 1, 'y': 2,
           'a': (partial(add, 1), 'x'),
           'b': (inc, 'a')}

    result = inline_functions(dsk, fast_functions=set([add]))
    assert 'a' not in set(result.keys())


def test_inline_doesnt_shrink_fast_functions_at_top():
    dsk = {'x': (inc, 'y'), 'y': 1}
    result = inline_functions(dsk, fast_functions=set([inc]))
    assert result == dsk


def test_inline_traverses_lists():
    x, y, i, d = 'xyid'
    dsk = {'out': (sum, [i, d]),
           i: (inc, x),
           d: (double, y),
           x: 1, y: 1}
    expected = {'out': (sum, [(inc, x), d]),
                d: (double, y),
                x: 1, y: 1}
    result = inline_functions(dsk, fast_functions=set([inc]))
    assert result == expected


def test_functions_of():
    a = lambda x: x
    b = lambda x: x
    c = lambda x: x
    assert functions_of((a, 1)) == set([a])
    assert functions_of((a, (b, 1))) == set([a, b])
    assert functions_of((a, [(b, 1)])) == set([a, b])
    assert functions_of((a, [[[(b, 1)]]])) == set([a, b])
    assert functions_of(1) == set()
    assert functions_of(a) == set()
    assert functions_of((a,)) == set([a])


def test_dealias():
    dsk = {'a': (range, 5),
           'b': 'a',
           'c': 'b',
           'd': (sum, 'c'),
           'e': 'd',
           'g': 'e',
           'f': (inc, 'd')}

    expected = {'a': (range, 5),
                'd': (sum, 'a'),
                'g': (identity, 'd'),
                'f': (inc, 'd')}

    assert dealias(dsk)  == expected


    dsk = {'a': (range, 5),
           'b': 'a',
           'c': 'a'}

    expected = {'a': (range, 5),
                'b': (identity, 'a'),
                'c': (identity, 'a')}

    assert dealias(dsk)  == expected


def test_equivalent():
    t1 = (add, 'a', 'b')
    t2 = (add, 'x', 'y')

    assert equivalent(t1, t1)
    assert not equivalent(t1, t2)
    assert equivalent(t1, t2, {'x': 'a', 'y': 'b'})
    assert not equivalent(t1, t2, {'a': 'x'})

    t1 = (add, (double, 'a'), (double, 'a'))
    t2 = (add, (double, 'b'), (double, 'c'))

    assert equivalent(t1, t1)
    assert not equivalent(t1, t2)
    assert equivalent(t1, t2, {'b': 'a', 'c': 'a'})
    assert not equivalent(t1, t2, {'b': 'a', 'c': 'd'})
    assert not equivalent(t2, t1, {'a': 'b'})

    # Test literal comparisons
    assert equivalent(1, 1)
    assert not equivalent(1, 2)
    assert equivalent((1, 2, 3), (1, 2, 3))


class Uncomparable(object):
    def __eq__(self, other):
        raise TypeError("Uncomparable type")


def test_equivalence_uncomparable():
    t1 = Uncomparable()
    t2 = Uncomparable()
    assert raises(TypeError, lambda: t1 == t2)
    assert equivalent(t1, t1)
    assert not equivalent(t1, t2)
    assert equivalent((add, t1, 0), (add, t1, 0))
    assert not equivalent((add, t1, 0), (add, t2, 0))


def test_sync_vars():
    dsk1 = {'a': 1, 'b': (add, 'a', 10), 'c': (mul, 'b', 5)}
    dsk2 = {'x': 1, 'y': (add, 'x', 10), 'z': (mul, 'y', 2)}
    assert sync_vars(dsk1, dsk2) == {'x': 'a', 'y': 'b'}
    assert sync_vars(dsk2, dsk1) == {'a': 'x', 'b': 'y'}

    dsk1 = {'a': 1, 'b': 2, 'c': (add, 'a', 'b'), 'd': (inc, (add, 'a', 'b'))}
    dsk2 = {'x': 1, 'y': 5, 'z': (add, 'x', 'y'), 'w': (inc, (add, 'x', 'y'))}
    assert sync_vars(dsk1, dsk2) == {'x': 'a'}
    assert sync_vars(dsk2, dsk1) == {'a': 'x'}


def test_sync_uncomparable():
    t1 = Uncomparable()
    t2 = Uncomparable()
    dsk1 = {'a': 1, 'b': t1, 'c': (add, 'a', 'b')}
    dsk2 = {'x': 1, 'y': t2, 'z': (add, 'y', 'x')}
    assert sync_vars(dsk1, dsk2) == {'x': 'a'}

    dsk2 = {'x': 1, 'y': t1, 'z': (add, 'y', 'x')}
    assert sync_vars(dsk1, dsk2) == {'x': 'a', 'y': 'b'}


def test_merge_sync():
    dsk1 = {'a': 1, 'b': (add, 'a', 10), 'c': (mul, 'b', 5)}
    dsk2 = {'x': 1, 'y': (add, 'x', 10), 'z': (mul, 'y', 2)}
    assert merge_sync(dsk1, dsk2) == {'a': 1, 'b': (add, 'a', 10),
            'c': (mul, 'b', 5), 'z': (mul, 'b', 2)}

    dsk1 = {'g1': 1,
            'g2': 2,
            'g3': (add, 'g1', 1),
            'g4': (add, 'g2', 1),
            'g5': (mul, (inc, 'g3'), (inc, 'g4'))}
    dsk2 = {'h1': 1,
            'h2': 5,
            'h3': (add, 'h1', 1),
            'h4': (add, 'h2', 1),
            'h5': (mul, (inc, 'h3'), (inc, 'h4'))}
    assert merge_sync(dsk1, dsk2) == {'g1': 1,
                                      'g2': 2,
                                      'g3': (add, 'g1', 1),
                                      'g4': (add, 'g2', 1),
                                      'g5': (mul, (inc, 'g3'), (inc, 'g4')),
                                      'h2': 5,
                                      'h4': (add, 'h2', 1),
                                      'h5': (mul, (inc, 'g3'), (inc, 'h4'))}
