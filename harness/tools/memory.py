"""harness.tools.memory — load_memory 工具。"""

from harness.tools.pool import register_tool, LOAD_MEMORY_SCHEMA

# Deferred init: cli.py calls init_memory_store(store)
_MEMORY_STORE = None


def init_memory_store(store):
    """由 cli.py 调用，注册 load_memory 工具。"""
    global _MEMORY_STORE
    _MEMORY_STORE = store

    def run_load_memory(name: str) -> str:
        if _MEMORY_STORE is None:
            return "Error: MemoryStore not initialized"
        return _MEMORY_STORE.load_memory(name)

    register_tool(LOAD_MEMORY_SCHEMA, run_load_memory)
