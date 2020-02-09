import time
import trio
import tractor


# this is the first 2 actors, streamer_1 and streamer_2
async def stream_data(seed):
    for i in range(seed):
        yield i
        await trio.sleep(0)  # trigger scheduler


# this is the third actor; the aggregator
async def aggregate(seed):
    """Ensure that the two streams we receive match but only stream
    a single set of values to the parent.
    """
    async with tractor.open_nursery() as nursery:
        portals = []
        for i in range(1, 3):
            # fork point
            portal = await nursery.start_actor(
                name=f'streamer_{i}',
                rpc_module_paths=[__name__],
            )

            portals.append(portal)

        send_chan, recv_chan = trio.open_memory_channel(500)

        async def push_to_chan(portal, send_chan):
            async with send_chan:
                async for value in await portal.run(
                    __name__, 'stream_data', seed=seed
                ):
                    # leverage trio's built-in backpressure
                    await send_chan.send(value)

            print(f"FINISHED ITERATING {portal.channel.uid}")

        # spawn 2 trio tasks to collect streams and push to a local queue
        async with trio.open_nursery() as n:

            for portal in portals:
                n.start_soon(push_to_chan, portal, send_chan.clone())

            # close this local task's reference to send side
            await send_chan.aclose()

            unique_vals = set()
            async with recv_chan:
                async for value in recv_chan:
                    if value not in unique_vals:
                        unique_vals.add(value)
                        # yield upwards to the spawning parent actor
                        yield value

                assert value in unique_vals

            print("FINISHED ITERATING in aggregator")

        await nursery.cancel()
        print("WAITING on `ActorNursery` to finish")
    print("AGGREGATOR COMPLETE!")


# this is the main actor and *arbiter*
async def main():
    # a nursery which spawns "actors"
    async with tractor.open_nursery() as nursery:

        seed = int(1e3)
        import time
        pre_start = time.time()

        portal = await nursery.run_in_actor(
            'aggregator',
            aggregate,
            seed=seed,
        )

        start = time.time()
        # the portal call returns exactly what you'd expect
        # as if the remote "aggregate" function was called locally
        result_stream = []
        async for value in await portal.result():
            result_stream.append(value)

        print(f"STREAM TIME = {time.time() - start}")
        print(f"STREAM + SPAWN TIME = {time.time() - pre_start}")
        assert result_stream == list(range(seed))
        return result_stream


if __name__ == '__main__':
    final_stream = tractor.run(main, arbiter_addr=('127.0.0.1', 1616))
