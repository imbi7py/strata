# -*- coding: utf-8 -*-

import common

from strata.core import Variable, LayerSet, ez_vars
from strata.config import ConfigSpec
from strata.layers import CLILayer


class VarOne(Variable):
    cli_arg_name = 'one'


class VarTwo(Variable):
    cli_arg_name = 'two'


def get_cli_config_spec(layerset=None):
    layerset = layerset or LayerSet('cli_set', [CLILayer])
    variables = [VarOne, VarTwo] + ez_vars(layerset)
    cspec = ConfigSpec(variables, layerset)
    return cspec


def get_cli_config(req_var_names=None):
    req_var_names = req_var_names or ['var_one', 'var_two']
    cspec = get_cli_config_spec()
    req_vars = [v for v in cspec.variables if v.name in req_var_names]
    return cspec.make_config(reqs=req_vars)


def test_cli():
    config = get_cli_config()
    config()
    return config


if __name__ == '__main__':
    test_cli()