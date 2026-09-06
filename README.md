# playlistamp

Rebuild a YouTube Music or Spotify playlist inside your own Plex library, as
closely as the library allows, so it shows up in Plexamp.

Plexamp is only a client — playlists live on the Plex Media Server. So this
reads the source's track list, matches each track against the music you already
own, and writes a Plex playlist. Plexamp picks it up on its own.

## Install

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # Windows
# source .venv/bin/activate && pip install -e . # macOS / Linux
```

## Set up

```bash
playlistamp config
```

It offers two ways to connect.

**Sign in at plex.tv (recommended).** It shows you a short code to enter at
<https://plex.tv/link>. Nothing else is needed — no IP address, no port, no
token. Your account already knows every way to reach the server, so this works
when the server is somewhere else entirely and you have no idea of its
address, and it handles Google/Apple sign-in and two-factor, which a username
and password cannot. It then lists the servers on your account, you pick one,
and it caches whichever address actually answered.

If that cached address later stops working — the remote address Plex hands out
does rotate — it silently re-discovers the server instead of failing.

**Enter an address and token yourself.** For a server you can reach directly.
The token is in Plex Web under any item → **Get Info** → **View XML**, as the
`X-Plex-Token` value in that tab's URL.

Settings live in `config.toml` in your platform config dir, readable only by
you. `PLEX_URL`, `PLEX_TOKEN` and `PLEX_SECTION` override it.

## The interface

```bash
playlistamp ui
```

Opens a local page at <http://127.0.0.1:5000>. On a first run it walks the same
two connection options as `playlistamp config` — the plex.tv code, or an
address and token — and then asks which music library to use.

After that: paste a YouTube Music or Spotify playlist link, press **Sync**, and
it fetches, matches, and writes the playlist. Confident matches go straight in.
Everything uncertain
lands in **Needs a look**, one card per track, showing the source track above
the closest thing in your library, with **Add** and **Ignore**:

```
YOUTUBE   Don't You Worry Child                            2:52
          City Sessions
──────────────────────────────────────────────────────────────
LIBRARY   Don't You Worry Child                            5:35
          Various Artists

▮▮▮▮▮▮▮▯▯▯  72   title 100 · artist 50 · length 0   [Ignore] [Add]
```

The bar breaks the score into the parts that produced it, so you can see *why*
it is unsure — here the title is perfect and the artist is a compilation tag,
which is a very different situation from a title that only half matches. Where
there are runners-up, "other possibilities" opens them and any one can be added
instead.

Press <kbd>A</kbd> to add or <kbd>I</kbd> to ignore the top card without
reaching for the mouse; the queue can run to dozens of tracks and this is much
faster. Decisions are saved as you go and pushed to Plex when you press
**Update playlist**. **Re-sync** re-runs the whole thing.

Every decision is remembered against the source's own track id, so a track you
have judged once never comes back — in this playlist or any other.

## Use from the terminal

```bash
playlistamp sync "https://music.youtube.com/playlist?list=PL..."
```

Spotify links work the same way:

```bash
playlistamp sync "https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U"
```

No login is needed for either service. YouTube Music serves public and unlisted
playlists without one; for Spotify, see below.

The first run pulls your whole music library down once and caches it; later
runs start instantly and refetch only when the library changes.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | print the full proposed mapping, write nothing |
| `--yes` | skip interactive review (for unattended runs) |
| `--name` | name the Plex playlist something other than the source's title |
| `--no-reorder` | update contents but leave your hand-curated order alone |
| `--auto-accept` / `--review-floor` | tune the match thresholds |
| `--refresh-index` | rebuild the library index now |

```bash
playlistamp index --refresh    # rebuild the cached library index
```

## How matching works

Both sides get reduced to a comparable form: `(Official Video)`, `- Remastered
2011`, `Artist - Topic` and similar noise is stripped, guest artists are pulled
out of the title, and accents and punctuation are folded away.

What is deliberately *not* stripped is anything marking a different recording —
`(Live)`, `(Acoustic)`, `(Demo)`, `(… Remix)`. Those become tags, and a
mismatch is penalised hard, because quietly matching a live cut to the studio
version is the usual way a tool like this goes wrong.

Candidates are then scored on title, artist, album and duration. Duration does
real work here: it separates the album cut from the extended mix when the
titles are identical. Artist carries heavy weight, so a perfect title with the
wrong artist loses.

User-curated playlists are full of uploads where the credited artist is really
the uploading channel and the actual artist sits in the title —
`"Steppenwolf - Born To Be Wild"` uploaded by `"Max Shkiv"`, or reversed in
`"Kashmir - Led Zeppelin"`. Both splits are tried and scored against your
library, so whichever reading is real wins on its own merits.

Scores at or above `--auto-accept` (default 88) match silently. Below
`--review-floor` (default 65) the track is reported missing. In between, you
are asked.

## Review, and why it only happens once

Borderline matches are shown with their candidates and a breakdown of why each
scored as it did:

```
1  84  Hotel California — Eagles          title 100 · artist 100 · album 60 · length 30
2  71  Hotel California — Eagles (live)   title 95 · artist 100 · album 40 · variant -18
```

`1`–`5` picks, `s` skips, `a` accepts the best for everything remaining, `q`
stops. Every decision is saved against the source's own track id, so a track
you have judged once is never raised again — in this playlist or any other. Repeat
syncs stay quiet.

## Re-running

Syncing the same playlist again updates it in place: new tracks are added,
dropped ones removed, and order restored. The playlist keeps its identity on
the server, so Plexamp keeps its artwork and position.

## Reports

Each run writes `report-<playlist>-<timestamp>.csv` — every track, its status,
what it matched, and the individual component scores. Anything your library
does not have also goes to `wanted-<playlist>-<timestamp>.txt` as a shopping
list.

## Tests

```bash
.venv/Scripts/python -m pytest
```

They run fully offline against fixtures — no server and no network.

## How Spotify is read

Spotify's February 2026 changes moved metadata endpoints off the Client
Credentials flow and put Developer Mode behind a Premium account, which is a
poor trade for reading a public playlist. So this uses what the web player
itself uses, and needs no account or app registration:

1. The public embed page carries a short-lived access token.
2. That token works against Spotify's pathfinder GraphQL endpoint, which pages
   through the entire playlist, 100 tracks at a time, with album names.
3. If that ever stops working, the embed page's own payload still lists the
   first 100 tracks. The playlist is then flagged as truncated — both the CLI
   and the UI say how many tracks of the total were read, rather than quietly
   syncing a partial playlist.

That token is scoped to embed playback and is refused by `api.spotify.com`
(`429 QUOTA_EXCEEDED`), which is why the GraphQL endpoint is used instead.

Step 2 depends on a persisted-query hash (`PLAYLIST_QUERY_HASH` in
`playlistamp/spotify.py`) because Spotify refuses raw queries. Spotify rotates
these occasionally; if full playlists stop working, updating that one constant
restores them, and until then step 3 keeps the tool usable.

## Limits

Only public playlists. Private ones, and YouTube's Liked Music, need a
logged-in session. For YouTube the plumbing is already there — `YTMusic()` in
`playlistamp/youtube.py` takes an `auth_file`, so wiring up a
`ytmusicapi browser` credential file is all that stands in the way.

Matching can only find what you already own. The tool never downloads anything.
