# -*- coding: utf-8 -*-

"""
# TODO: raise exception on **kwarg usage in Provider?

words:

provider -> (layer, arg_names (aka deps))
consumer
dependency
arg[ument]
satisfy
unsatisfied
pruned
slot
stack

process notes:

* precursor reconciliation: provider argument names with known Variable names

"requirements" are Variables that must be successfully provided during
Config instantiation. If requirements isn't explicitly provided, it's
assumed that all Variables must be provided for a Config object to
successfully instantiate.

Put another way, at the ConfigSpec level, all variables need to have
at least one Provider, but the existence of a Provider doesn't mean
that the Provider will actually produce a value suitable for a given
Variable. The "requirements" construct provides a mechanism for
allowing a Config to generate an exception if a Variable is
unprovided, and allow other Variables to pass unprovided.

* How to differentiate between variables that are required, variables
  that are optional (will be tried not an error if not provided), and
  pruned variables (ones that aren't required and aren't dependencies
  of any other variables).
* "Required" if no default value is provided. This has to be known at
  ConfigSpec time.

* TODO: is it ok to keep a reference to ConfigProcessor instance (and
  all the providers)?
* TODO: die on provided values that don't validate or continue on to
  next provider?

"""

from itertools import chain
from collections import deque

from .core import DEBUG, Provider
from .utils import inject
from .errors import (ConfigException,
                     NotProvidable,
                     DependencyCycle,
                     UnresolvedDependency)
from .layers import StrataConfigLayer, StrataDefaultLayer

from .tableutils import Table


class Resolution(object):
    def __init__(self, by, value=None):
        self.by = by
        self.value = value

    def __repr__(self):
        cn = self.__class__.__name__
        return '%s(by=%r, value=%r)' % (cn, self.by, self.value)


class Pruned(Resolution):
    def __init__(self, by=None, value=None):
        return super(Pruned, self).__init__(by, value)


class Satisfied(Resolution):
    pass


class Unsatisfied(Resolution):
    pass


class ConfigSpec(object):
    def __init__(self, variables, layers):
        self._input_layers = list(layers or [])
        self.layers = ([StrataConfigLayer]
                       + self._input_layers
                       + [StrataDefaultLayer])

        self._input_variables = list(variables or [])

        ap_vars = [layer._get_autoprovided() for layer in self.layers]
        self._autoprovided_variables = list(chain(*ap_vars))
        self.variables = self._input_variables + self._autoprovided_variables

        self.name_var_map = dict([(v.name, v) for v in self.variables])
        self._compute()

    @classmethod
    def from_modules(cls, modules):
        """find all variables/layers in the modules.
        One ConfigSpec per layer list.

        TODO: except/warn on overwrites/unused types?
        """
        return cls([], [])

    def make_config(self, name=None, default_defer=False):
        name = name or 'Config'
        attrs = {'_config_spec': self,
                 '_default_defer': default_defer}
        return type(name, (BaseConfig,), attrs)

    def _compute(self):
        vpm = self.var_provider_map = {}
        vcm = self.var_consumer_map = {}
        layers, variables = self.layers, self.variables
        name_var_map = dict(self.name_var_map)

        to_proc = [v.name for v in variables]
        unresolved = []
        while to_proc:
            cur_var_name = to_proc.pop()
            var = name_var_map[cur_var_name]
            for layer in layers:
                try:
                    provider = layer._get_provider(var)
                except NotProvidable:
                    continue
                vpm.setdefault(var.name, []).append(provider)
                for dn in provider.dep_names:
                    vcm.setdefault(dn, []).append(provider)
                    if dn not in name_var_map:
                        unresolved.append(dn)
                        name_var_map[dn] = None
                        #to_proc.append(dn)
            if cur_var_name not in vpm:
                raise UnresolvedDependency('no providers found for: %r' % var)
        if unresolved:
            raise UnresolvedDependency('unresolved deps: %r' % unresolved)
        self.all_providers = sum(vpm.values(), [])
        self.all_var_names = sorted(vpm.keys())

        sdm = self.slot_dep_map = self._compute_slot_dep_map(vpm)
        srdm = self.slot_rdep_map = self._compute_rdep_map(sdm)
        sorted_dep_slots = jit_toposort(srdm)
        dep_indices, slot_order = {}, []
        for level_idx, level in enumerate(sorted_dep_slots):
            for var_name in level:
                dep_indices[var_name] = level_idx
                slot_order.append(var_name)
        self.slot_order = slot_order

    @staticmethod
    def _compute_slot_dep_map(var_provider_map, preprovided=None):
        preprovided = preprovided or set()
        slot_dep_map = {}  # args across all layers
        for var, providers in var_provider_map.items():
            if var in preprovided:
                slot_dep_map[var] = set()
            else:
                slot_deps = sum([list(p.dep_names) for p in providers], [])
                slot_dep_map[var] = set(slot_deps)
        return slot_dep_map

    @staticmethod
    def _compute_rdep_map(dep_map):
        "compute recursive dependency map"
        rdep_map = {}
        for var, slot_deps in dep_map.items():
            to_proc, rdeps, i = [var], set(), 0
            while to_proc:
                i += 1  # TODO: better circdep handlin
                if i > 50:
                    msg = ('dependency cycle: %r recursively depends on %r'
                           % (var, sorted(rdeps)))
                    raise DependencyCycle(msg)
                cur = to_proc.pop()
                cur_rdeps = dep_map.get(cur, [])
                to_proc.extend([c for c in cur_rdeps if c not in to_proc])
                rdeps.update(cur_rdeps)
            rdep_map[var] = rdeps
        return rdep_map


class ConfigProcessor(object):
    def __init__(self, config, debug=DEBUG):
        self.config = config
        self.requirements = self.config._config_spec.variables
        self.req_names = set([v.name for v in self.requirements])

        self.name_value_map = {}
        self.name_satisfier_map = {}
        self.name_result_map = {}  # only stores most recent result
        self.provider_result_map = {}
        self._debug = debug

        self._init_layers()
        self._init_providers()

    def _init_layers(self):
        # this whole thing is pretty TODO
        self._strata_config_layer = StrataConfigLayer(self.config)
        self._strata_default_layer = StrataDefaultLayer()
        layer_type_pairs = [(StrataConfigLayer, self._strata_config_layer)]
        layer_type_pairs.extend([(t, t()) for t in
                                 self.config._config_spec.layers[1:-1]])
        layer_type_pairs.append((StrataDefaultLayer,
                                 self._strata_default_layer))
        self.layers = [ltp[1] for ltp in layer_type_pairs]
        self.layer_map = dict(layer_type_pairs)

    def _init_providers(self):
        vpm = self.config._config_spec.var_provider_map
        bpm = self.bound_provider_map = {}
        bpl = self.bound_provider_list = []
        for name, provider_list in vpm.items():
            bound_providers = [cp.get_bound(self.layer_map[cp.layer_type])
                               for cp in provider_list]
            bpm[name] = bound_providers
        # TODO: cleaner way to make config_provider ?
        config_provider = Provider(self._strata_config_layer,
                                   'config',
                                   lambda: self.config)
        bpm['config'] = [config_provider]
        self.satisfy(config_provider, self.config)
        bpl.extend(chain(*bpm.values()))

    def _build_error(self, var_name):
        # provide -> satisfy?
        consumers = self.config._config_spec.var_consumer_map[var_name]
        consumer_names = [v.var_name for v in consumers]
        msg = ('could not provide %r, required by %r, '
               'encountered the following errors:'
               % (var_name, consumer_names))
        lines = [msg]
        lines.extend([' - %s: %r' % (e.by.layer_type.__name__, e.value)
                      for e in self.name_result_map[var_name]])
        return '\n'.join(lines)

    def process(self):
        bpm, prm = self.bound_provider_map, self.provider_result_map
        nrm, nvm = self.name_result_map, self.name_value_map

        to_proc = deque(chain(*[bpm[var_name] for var_name in self.req_names]))
        while to_proc:
            cp = to_proc.popleft()
            if cp in prm:
                continue  # already run/memoized
            if cp.var_name in nvm:
                continue  # already satisfied
            unsat_deps = [dep for dep in cp.dep_names if dep not in nvm]
            if unsat_deps:
                to_proc.appendleft(cp)  # repushing current
                for dep_name in unsat_deps:
                    if (len(nrm.get(dep_name, [])) >= len(bpm[dep_name])):
                        msg = self._build_error(dep_name)
                        raise ValueError(msg)
                    to_proc.extendleft(bpm[dep_name])
                continue
            try:
                value = inject(cp.func, nvm)
            except Exception as e:
                self.unsatisfy(cp, e)
            else:
                _var = self.config._config_spec.name_var_map[cp.var_name]
                _var = _var()  # TODO
                processed_value = _var.process_value(value)
                self.satisfy(cp, processed_value)  # save unprocessed

        for bp in self.bound_provider_list:
            if bp not in prm:
                self.prune(bp, '<no refs>')

    def is_satisfied(self, var_name):
        return var_name in self.name_value_map

    def satisfy(self, provider, value):
        # satisfy is the only one that actually updates the scope
        result = Satisfied(by=provider, value=value)
        self.name_value_map[provider.var_name] = value
        self.name_satisfier_map[provider.var_name] = provider
        bps = self.bound_provider_map[provider.var_name]
        pruned_bps = bps[bps.index(provider) + 1:]
        for pbp in pruned_bps:
            self.prune(pbp, '<already satisfied>')
        return self.register_result(provider, result)

    def prune(self, provider, value):
        result = Pruned(by=provider, value=value)
        if self._debug:
            print ' == ', result
        return self.register_result(provider, result)

    def unsatisfy(self, provider, exception):
        result = Unsatisfied(by=provider, value=exception)
        if self._debug:
            print ' - ', result
        return self.register_result(provider, result)

    def register_result(self, provider, result):
        self.name_result_map.setdefault(provider.var_name, []).append(result)
        self.provider_result_map[provider] = result
        return result

    def __repr__(self):
        return ('<%s: %s providers, %s variables, %s satisfied>'
                % (self.__class__.__name__,
                   len(self.provider_result_map),
                   len(self.name_result_map),
                   len(self.name_value_map)))

    def to_table(self):
        lookup = {}
        for bp in self.bound_provider_list:
            lookup.setdefault(bp.layer_type, {})[bp.var_name] = bp
        sorted_vars = self.config._config_spec.slot_order
        lol = [[''] + sorted_vars]
        for layer in self.layers:
            layer_type = layer.__class__
            cur_row = [layer_type.__name__]
            for var_name in sorted_vars:
                try:
                    cur_provider = lookup[layer_type][var_name]
                except KeyError:
                    val = ''
                else:
                    try:
                        res = self.provider_result_map[cur_provider]
                        if isinstance(res, Satisfied):
                            val = res.value
                        else:
                            val = 'X'
                    except KeyError:
                        val = '-'
                cur_row.append(val)
            lol.append(cur_row)
        return Table(lol)


class BaseConfig(object):
    _config_spec = None
    _default_defer = False
    _config_proc_type = ConfigProcessor

    def __init__(self, **kwargs):
        self._deferred = kwargs.pop('_defer', self._default_defer)
        self._input_kwargs = dict(kwargs)
        if not self._deferred:
            self._process()

    def __repr__(self):
        # would a non-constructor style repr be more helpful?
        cn = self.__class__.__name__
        kw_str = ', '.join(['%s=%r' % (k, v) for k, v
                            in self._input_kwargs.items()])
        return '%s(%s)' % (cn, kw_str)

    def _pre_process(self):
        pass

    def _post_process(self):
        self.__dict__.update(self._result_map)

    def _process(self):
        self._config_proc = self._config_proc_type(config=self)
        self._pre_process()

        self._config_proc.process()
        self._result_map = self._config_proc.name_value_map
        self._provider_results = self._config_proc.provider_result_map

        req_names = set([v.name for v in self._config_spec.variables])
        self._unresolved = req_names - set(self._result_map)

        if self._unresolved:
            sorted_unres = sorted(self._unresolved)
            raise ConfigException('could not resolve: %r' % sorted_unres)
        if DEBUG:
            print self._config_proc
        self._post_process()


def toposort(dep_map):
    "expects a dict of {item: set([deps])}"
    ret, dep_map = [], dict(dep_map)
    if not dep_map:
        return []
    extras = set.union(*dep_map.values()) - set(dep_map)
    dep_map.update([(k, set()) for k in extras])
    remaining = dict(dep_map)
    while remaining:
        cur = set([item for item, deps in remaining.items() if not deps])
        if not cur:
            break
        ret.append(cur)
        remaining = dict([(item, deps - cur) for item, deps
                          in remaining.items() if item not in cur])
    if remaining:
        raise ValueError('unresolvable dependencies: %r' % remaining)
    return ret


def jit_toposort(dep_map):
    "expects a dict of {item: set([deps])}"
    ret, orig_dep_map, dep_map = [], dep_map, dict(dep_map)
    if not dep_map:
        return []
    extras = set.union(*dep_map.values()) - set(dep_map)
    dep_map.update([(k, set()) for k in extras])
    remaining = dict(dep_map)
    ready = set()
    while remaining:
        cur = set([item for item, deps in remaining.items() if not deps])
        if not cur:
            break
        ready.update(cur)
        cur_used = set([r for r in ready
                        if any([r in orig_dep_map[c] for c in cur])])
        ret.append(cur_used)
        ready = ready - cur_used
        remaining = dict([(item, deps - cur) for item, deps
                          in remaining.items() if item not in cur])
    if ready:
        ret.append(ready)
    if remaining:
        raise ValueError('unresolvable dependencies: %r' % remaining)
    return ret[1:]  # nothing's every used before the first thing, so snip snip
