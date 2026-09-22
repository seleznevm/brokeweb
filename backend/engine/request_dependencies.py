"""Source declarations needed to evaluate a request expression in its own context.

The pinned requests depend on immutable scalar declarations and pure functions.
Mutable global dependencies are deliberately rejected rather than borrowed from
the chart, which would silently change Pine's requested-context calculations.
"""
from .interpreter import qualified, PineRuntimeError


def declarations_for(program, expression):
    declarations = {s.meta['name']: s for s in program.statements if s.kind == 'assign'}
    mutable = set()

    def mutations(statements):
        for s in statements:
            if s.kind == 'assign' and s.meta['op'] != '=':
                mutable.add(s.meta['name'])
            mutations(s.body)
            mutations(s.otherwise)
    mutations(program.statements)

    def names(node, locals=frozenset(), stack=()):
        if node.kind == 'name':
            return {node.value} - locals
        if node.kind == 'call':
            result = set().union(*(names(a, locals, stack) for a in node.args[1:]))
            function = qualified(node.args[0])
            if function in program.functions:
                if function in stack:
                    raise PineRuntimeError('Recursive request dependency')
                fn = program.functions[function]
                local = set(fn.meta['params'])

                def local_names(body):
                    for s in body:
                        if s.kind == 'assign': local.add(s.meta['name'])
                        if s.kind == 'for': local.add(s.meta['name'])
                        local_names(s.body); local_names(s.otherwise)
                local_names(fn.body)

                def free(body):
                    out = set()
                    for s in body:
                        if s.expr: out |= names(s.expr, local, (*stack, function))
                        out |= free(s.body) | free(s.otherwise)
                    return out
                result |= free(fn.body)
            return result
        return set().union(*(names(a, locals, stack) for a in node.args))

    needed, visiting = set(), set()

    def visit(name):
        if name in needed or name not in declarations:
            return
        if name in visiting:
            raise PineRuntimeError('Cyclic request dependency: ' + name)
        s = declarations[name]
        if name in mutable or s.meta['mode'] in ('var', 'varip') or name.startswith('['):
            raise PineRuntimeError('Unsupported mutable request dependency: ' + name)
        visiting.add(name)
        for dependency in names(s.expr): visit(dependency)
        visiting.remove(name)
        needed.add(name)
    for name in names(expression): visit(name)
    return [s for s in program.statements if s.kind == 'assign' and s.meta['name'] in needed]
