# -*- coding: utf-8 -*-

import sys  # TODO: haaaack
from os.path import abspath, dirname as dn
sys.path.append(dn(dn(dn(abspath(__file__)))))


#from strata.config import Config
from strata.core import Variable, Layer


class VarA(Variable):
    pass


class VarB(Variable):
    pass


class FirstLayer(Layer):
    #def __init__(self, stuff):  # TODO: support injecting kwargs here?
    #    self.stuff = stuff

    def var_a(self):
        return 0

    def var_b(self, var_a):
        if var_a:
            return 1
        else:
            raise KeyError('nope')

    def var_c(self):
        return 3


class SecondLayer(Layer):
    def var_b(self):
        return 2

    def var_d(self, var_b, var_c):
        return 4


class ThirdLayer(Layer):
    def var_a(self):
        return -1  # should never get here
