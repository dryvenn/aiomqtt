import functools
import asyncio

import paho.mqtt.client as mqtt


class MQTTMessageInfo(object):

    def __init__(self, loop, mqtt_message_info):
        self._loop = loop
        self._mqtt_message_info = mqtt_message_info

    def __getattr__(self, name):
        return getattr(self._mqtt_message_info, name)

    async def wait_for_publish(self):
        return await self._loop.run_in_executor(
            None, self._mqtt_message_info.wait_for_publish)

    def __iter__(self):
        return iter(self._mqtt_message_info)

    def __getitem__(self, i):
        return self._mqtt_message_info[i]

    def __str__(self):
        return str(self._mqtt_message_info)


class Client(object):
    """
    An AsyncIO based wrapper around the paho-mqtt MQTT client class.

    Essentially, the differences between this and the paho.mqtt.client.Client
    are:

    * The constructor takes an asyncio loop to use as the first argument.
    * Blocking methods (connect, connect_srv, reconnect_delay_set) are now
      coroutines.
    * Callback functions are always safely inserted into the asyncio event loop
      rather than being run from an unspecified thread, however the loop is
      started.
    """

    def __init__(self, loop=None, *args, **kwargs):
        self._loop = loop or asyncio.get_running_loop()
        self._client = mqtt.Client(*args, **kwargs)

        self._wrap_blocking_method("connect")
        self._wrap_blocking_method("connect_srv")
        self._wrap_blocking_method("reconnect")

        self._wrap_blocking_method("loop_forever")
        self._wrap_blocking_method("loop_stop")

        self._wrap_callback("on_connect")
        self._wrap_callback("on_disconnect")
        self._wrap_callback("on_message")
        self._wrap_callback("on_publish")
        self._wrap_callback("on_subscribe")
        self._wrap_callback("on_unsubscribe")
        self._wrap_callback("on_log")

        self._client.on_socket_open = self._on_socket_open
        self._client.on_socket_close = self._on_socket_close
        self._client.on_socket_register_write = self._on_socket_register_write
        self._client.on_socket_unregister_write = self._on_socket_unregister_write

    ###########################################################################
    # External event loop callbacks
    ###########################################################################

    def _on_socket_open(self, client, userdata, sock):
        self._loop.add_reader(sock, client.loop_read)
        self._misc_task = asyncio.run_coroutine_threadsafe(self._misc_loop(), self._loop)

    def _on_socket_close(self, client, userdata, sock):
        self._loop.remove_reader(sock)
        self._misc_task.cancel()

    def _on_socket_register_write(self, client, userdata, sock):
        self._loop.add_writer(sock, client.loop_write)

    def _on_socket_unregister_write(self, client, userdata, sock):
        self._loop.remove_writer(sock)

    async def _misc_loop(self):
        while self._client.loop_misc() == mqtt.MQTT_ERR_SUCCESS:
            await asyncio.sleep(1)

    ###########################################################################
    # Utility functions for creating wrappers
    ###########################################################################

    def _wrap_callback(self, name):
        """Add the named callback to the MQTT client which triggers a call to
        the wrapper's registered callback in the event loop thread.
        """
        setattr(self, name, None)

        def wrapper(_client, *args):
            f = getattr(self, name)
            if f is not None:
                self._loop.call_soon_threadsafe(f, self, *args)
        setattr(self._client, name, wrapper)

    def _wrap_blocking_method(self, name):
        """Wrap a blocking function to make it async."""
        f = getattr(self._client, name)

        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            return await self._loop.run_in_executor(
                None, functools.partial(f, *args, **kwargs))
        setattr(self, name, wrapper)

    def __getattr__(self, name):
        """Fall back on non-wrapped versions of most functions."""
        return getattr(self._client, name)

    ###########################################################################
    # Special-case wrappers around certain methods
    ###########################################################################

    @functools.wraps(mqtt.Client.publish)
    def publish(self, *args, **kwargs):
        # Produce an alternative MQTTMessageInfo object with a coroutine
        # wait_for_publish.
        return MQTTMessageInfo(
            self._loop, self._client.publish(*args, **kwargs))

    @functools.wraps(mqtt.Client.message_callback_add)
    def message_callback_add(self, sub, callback):
        # Ensure callbacks are called from MQTT
        @functools.wraps(callback)
        def wrapper(_client, *args, **kwargs):
            self._loop.call_soon_threadsafe(
                functools.partial(callback, self, *args, **kwargs))
        self._client.message_callback_add(sub, wrapper)
