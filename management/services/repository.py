from collections.abc import MutableMapping


class LegacyServices(MutableMapping):
    """Compatibility projection; callers get detached snapshots, never live dictionaries."""
    def __init__(self,store):self.store=store
    def __getitem__(self,key):
        value=self.store.get('service',key)
        if value is None:raise KeyError(key)
        return value
    def __setitem__(self,key,value):
        old=self.store.get('service',key)
        self.store.save('service',key,value,old['version'] if old else 0,reason='兼容服务登记')
    def __delitem__(self,key):raise TypeError('Use a versioned lifecycle transition instead of deletion')
    def __iter__(self):return iter([v['id'] for v in self.store.list('service')])
    def __len__(self):return len(self.store.list('service'))
