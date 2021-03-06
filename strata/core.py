# -*- coding: utf-8 -*-

from types import MethodType

DEBUG = False

from .utils import under2camel, camel2under, get_arg_names
from .errors import MissingValue, ProviderError, NotProvidable


class VariableMeta(type):
    def __new__(mcls, name, bases, attrs):
        n_attr = attrs.get('name')
        if not n_attr:
            n_attr = attrs['name'] = camel2under(name)
        if n_attr.startswith('_'):
            msg = 'Variable name cannot start with underscore: %r' % n_attr
            raise TypeError(msg)
        cls = super(VariableMeta, mcls).__new__(mcls, name, bases, attrs)

        cls.description = getattr(cls, 'description', '') or cls.__doc__ or ''
        default_summary = (cls.description.splitlines() or [''])[0][:60]
        cls.summary = getattr(cls, 'summary', '') or default_summary

        return cls


class Variable(object):
    __metaclass__ = VariableMeta

    name = None
    validator = None

    def get_default(self):
        try:
            return self.default
        except AttributeError:
            raise MissingValue('no default specified for: %s' % self.name)

    def process_value(self, value):
        if self.validator:
            return self.validator(value)
        return value


class BaseLayer(object):
    @classmethod
    def _get_provider(cls, variable):
        raise NotImplementedError('Layers must implement _get_provider()')


class Layer(object):
    @classmethod
    def _get_provider(cls, variable):
        vn = variable.name
        try:
            func = getattr(cls, vn)
        except AttributeError:
            raise NotProvidable(cls, variable)
        # TODO: what to do if func not callable (accidental missing underscore)
        return Provider(cls, vn, func)

    @classmethod
    def _specialize(cls, prefix=None):
        pass

    @classmethod
    def _get_autoprovided(cls):
        """
        returns Variable instances for automatically provided
        variables within a Layer.
        """
        cn = cls.__name__
        # get explicit autoprovides
        eap = getattr(cls, '_autoprovided', [])
        ap_var_map, unknown_eaps = {}, []
        for obj in eap:
            try:
                if issubclass(obj, Variable):
                    ap_var_map[obj.name] = obj
                    continue
            except TypeError:
                pass
            if isinstance(obj, basestring):
                ap_var_map[obj] = None
            else:
                unknown_eaps.append(obj)
        if unknown_eaps:
            raise TypeError('Layer %s has unsupported autoprovide types: %r'
                            % (cn, unknown_eaps))

        for attrname in dir(cls):
            if attrname in ap_var_map and ap_var_map[attrname] is not None:
                continue  # already has a variable associated with it
            attr = getattr(cls, attrname)
            try:
                auto_var = attr._autoprovided_variable
            except AttributeError:
                if attrname in ap_var_map and isinstance(attr, MethodType):
                    auto_var = func2variable(attr.im_func)
                else:
                    continue
            ap_var_map[attrname] = auto_var

        unconverted = [an for an, var in ap_var_map.items() if var is None]
        if unconverted:
            raise TypeError('unable to resolve %s autoprovided variables: %r'
                            % (cn, unconverted))
        return ap_var_map.values()

    def __repr__(self):
        return '%s()' % self.__class__.__name__


class Provider(object):
    """\
    Used internally to represent a single Layer instance's implementation
    of a single Variable. (the intersection of Layer and Variable).
    """

    def __init__(self, layer, var_name, func):
        if isinstance(layer, type):
            self.layer_inst = None
            self.layer_type = layer
        else:
            self.layer_inst = layer
            self.layer_type = type(layer)
        self.var_name = var_name
        self.func = func
        try:
            self.dep_names = get_arg_names(self.func)
        except:
            raise ProviderError('unsupported provider type: %r' % self.func)

    @property
    def is_bound(self):
        return self.layer_inst is not None

    def get_bound(self, layer_inst):
        func, var_name, layer_type = self.func, self.var_name, self.layer_type
        # first, a sanity check
        if not isinstance(layer_inst, layer_type):
            raise TypeError('expected an instance of %r, not %r'
                            % (layer_inst, layer_type))
        # do the actual method rebind (technically sorta the first binding)
        try:
            if func.im_self is None and isinstance(layer_inst, func.im_class):
                func = type(func)(func.im_func, layer_inst, layer_type)
        except AttributeError:
            pass
        p_type = type(self)
        return p_type(layer_inst, var_name, func)

    def __repr__(self):
        cn = self.__class__.__name__
        try:
            layer_cn = self.layer_type.__name__
            func_sig = '%s(%s)' % (self.var_name, ', '.join(self.dep_names))
            return '%s(%s.%s)' % (cn, layer_cn, func_sig)
        except:
            return super(Provider, self).__repr__()


class FileValue(object):
    def __init__(self, value, file_path):
        self.value = value
        self.file_path = file_path


def ez_vars(layers):
    """
    A (most likely temporary) utility function to make Variables off
    of Layer definitions. Something like this should maybe exist in
    the future, using decorators.
    """
    names = set()
    for layer in layers:
        for name in dir(layer):
            if name.startswith('_'):
                continue
            names.add(under2camel(name))
    return [VariableMeta(n, (Variable,), {}) for n in sorted(names)]


def func2variable(func, class_name=None, **kwargs):
    "expects a function, not a bound/unbound method."
    var_name = func.func_name
    class_name = class_name or under2camel(var_name)
    attrs = dict(kwargs, name=var_name)
    attrs.setdefault('description', func.func_doc)
    variable = VariableMeta(class_name, (Variable,), attrs)
    return variable


def autoprovide(*args, **kwargs):
    attrs = {'validator': kwargs.pop('validator', None),
             'description': kwargs.pop('description', None),
             'summary': kwargs.pop('summary', None)}
    if kwargs:
        raise TypeError('got unexpected keyword arguments: %r' % kwargs.keys())

    def autoprovide_attr_assigner(func):
        variable = func2variable(func, **attrs)
        func._autoprovided_variable = variable
        return func

    if args:
        func = args[0]
        if callable(func):
            return autoprovide_attr_assigner(func)
        else:
            raise TypeError('autoprovide expects to be called as a decorator'
                            ' on a function, not %r' % func)
    else:
        return autoprovide_attr_assigner
