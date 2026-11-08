"""One native writer; intents stay pending until its atomic batch succeeds."""
class BufferedDB:
    def __init__(self,database,enabled=True):self.database=database;self.enabled=enabled;self.pending={}
    def get(self,key):return self.pending[key] if key in self.pending else self.database.get(key)
    def putBatch(self,values):
        if self.enabled:self.pending.update(values)
        else:self.database.putBatch(values)
    def iterate(self):return self.database.iterate()
    def flush(self):
        if self.pending:self.database.putBatch(dict(sorted(self.pending.items())));self.pending={}
    def close(self):
        # On failure, unfinished intents remain in SQLite for retry. Never
        # publish a partially computed batch from an exception/finally block.
        self.pending={};self.database.close()
