"""
RPC related
"""
import itertools

import pytest
import tractor
import trio


async def sleep_back_actor(
    actor_name,
    func_name,
    func_defined,
    exposed_mods,
):
    if actor_name:
        async with tractor.find_actor(actor_name) as portal:
            try:
                await portal.run(__name__, func_name)
            except tractor.RemoteActorError as err:
                expect = tractor.ModuleNotExposed
                if not func_defined:
                    expect = AttributeError

                assert err.type is expect
                raise
    else:
        await trio.sleep(float('inf'))


async def short_sleep():
    await trio.sleep(0)


@pytest.mark.parametrize(
    'to_call', [
        ([], 'short_sleep', tractor.RemoteActorError),
        ([__name__], 'short_sleep', tractor.RemoteActorError),
        ([__name__], 'fake_func', tractor.RemoteActorError),
        (['tmp_mod'], 'import doggy', ModuleNotFoundError),
        (['tmp_mod'], '4doggy', SyntaxError),
    ],
    ids=['no_mods', 'this_mod', 'this_mod_bad_func', 'fail_to_import',
         'fail_on_syntax'],
)
def test_rpc_errors(arb_addr, to_call, testdir):
    """Test errors when making various RPC requests to an actor
    that either doesn't have the requested module exposed or doesn't define
    the named function.
    """
    exposed_mods, funcname, inside_err = to_call
    subactor_exposed_mods = []
    func_defined = globals().get(funcname, False)
    subactor_requests_to = 'arbiter'
    remote_err = tractor.RemoteActorError

    # remote module that fails at import time
    if exposed_mods == ['tmp_mod']:
        # create an importable module with a bad import
        testdir.syspathinsert()
        # module should raise a ModuleNotFoundError at import
        testdir.makefile('.py', tmp_mod=funcname)

        # no need to exposed module to the subactor
        subactor_exposed_mods = exposed_mods
        exposed_mods = []
        func_defined = False
        # subactor should not try to invoke anything
        subactor_requests_to = None
        # the module will be attempted to be imported locally but will
        # fail in the initial local instance of the actor
        remote_err = inside_err

    async def main():
        actor = tractor.current_actor()
        assert actor.is_arbiter

        # spawn a subactor which calls us back
        async with tractor.open_nursery() as n:
            await n.run_in_actor(
                'subactor',
                sleep_back_actor,
                actor_name=subactor_requests_to,
                # function from the local exposed module space
                # the subactor will invoke when it RPCs back to this actor
                func_name=funcname,
                exposed_mods=exposed_mods,
                func_defined=True if func_defined else False,
                rpc_module_paths=subactor_exposed_mods,
            )

    def run():
        tractor.run(
            main,
            arbiter_addr=arb_addr,
            rpc_module_paths=exposed_mods,
        )

    # handle both parameterized cases
    if exposed_mods and func_defined:
        run()
    else:
        # underlying errors aren't propagated upwards (yet)
        with pytest.raises(remote_err) as err:
            run()

        # get raw instance from pytest wrapper
        value = err.value

        # might get multiple `trio.Cancelled`s as well inside an inception
        if isinstance(value, trio.MultiError):
            value = next(itertools.dropwhile(
                lambda exc: not isinstance(exc, tractor.RemoteActorError),
                value.exceptions
            ))

        if getattr(value, 'type', None):
            assert value.type is inside_err
