# Auto-cache Opening Segment on API startup

The Opening Segment — the story from game start to the first Player
Choice — is identical across every Playthrough of the same Script Pack
Version because no player action precedes it. The API now generates and
caches it in the background on startup via a FastAPI lifespan hook, so
the first player never waits for opening generation. The server starts
serving immediately; warmup runs concurrently and failures fall back to
live generation silently. Pack hash changes invalidate the cache
automatically.
