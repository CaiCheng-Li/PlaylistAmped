# playlistamped

Rebuild a YouTube Music or Spotify playlist inside your own Plex library, as
closely as the library allows, so it turns up in Plexamp.

Plexamp is only a client — playlists live on the Plex Media Server. So this
reads the source's track list, matches each track against music you already
own, and writes a Plex playlist. Plexamp picks it up on its own.

Everything happens in a local web interface: connecting to Plex, syncing,
reviewing uncertain matches, switching servers, and signing out.

```
┌──────────────────────────────────────────────────────────────┐
│ SPOTIFY   Stairway to Heaven - Remaster              8:03    │
│           Led Zeppelin                                       │
│ ──────────────────────────────────────────────────────────── │
│ LIBRARY   Stairway to Heaven                         8:18    │
│           Dread Zeppelin · 5,000,000*                        │
│                                                              │
│ ▮▮▮▮▮▮▮▮▯▯  86  title 100 · artist 85 · length 56            │
│                                        [ Ignore ]  [ Add ]   │
└──────────────────────────────────────────────────────────────┘
```

That is the whole idea: a perfect title match, by a parody band, held back for
you to judge rather than quietly added.

## Requirements

- Python 3.11 or newer
- A Plex Media Server with a music library
- Works on Linux, macOS and Windows

## Install

```bash
git clone https://github.com/CaiCheng-Li/PlaylistAmped.git
cd PlaylistAmped

python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

pip install -e .
```

## Run

```bash
playlistamped
```

It starts a local server on <http://127.0.0.1:7391> and opens your browser.
If that port is taken it picks the next free one. `--port`, `--host` and
`--no-browser` are there if you need them; those flags are the only command
line the program has.

It binds to localhost only by default, deliberately — the page can reach your
Plex token, so it should not be served to the network.

## Connecting to Plex

On the first run you get two choices.

**Sign in with a code at plex.tv.** It shows a short code to enter at
<https://plex.tv/link>. Nothing else is needed — no IP address, no port, no
token. Your account already knows every route to your servers, so this works
when the server is somewhere else entirely and you have no idea of its
address, and it copes with Google/Apple sign-in and two-factor, which a
username and password cannot. This is also what lets you switch servers later.

**Enter a server address and token.** For a server you can reach directly.
The token is in Plex Web under any item → **Get Info** → **View XML**, as the
`X-Plex-Token` value in that tab's URL. This route reaches only that one
server.

Either way you then pick which music library to use.

## Syncing

Paste a public YouTube Music or Spotify playlist link and press **Sync**. No
sign-in is needed for either service.

```
https://music.youtube.com/playlist?list=PL...
https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U
```

The first run downloads your whole music library once and caches it; later
runs start instantly and refetch only when the library changes.

Under **Options**:

| Option | Effect |
|---|---|
| Playlist name in Plex | Use a different name from the source playlist's own |
| Preview only | Match and show the result without writing anything to Plex |
| Keep the existing order | Update contents but leave an order you curated by hand |

**Re-sync** refetches the playlist and matches again, picking up tracks added
at the source. Syncing the same playlist again updates it in place — the
playlist keeps its identity on the server, so Plexamp keeps its artwork and
position.

## Reviewing

Confident matches go straight in. Anything uncertain lands in **Needs a look**,
one card per track: the source track above the closest thing in your library,
with **Add** and **Ignore**.

The bar breaks the score into the parts that produced it, so you can see *why*
it is unsure. `title 100 · artist 50` means a perfect title against a
compilation tag — a very different situation from a title that only half
matches. Where there are runners-up, "other possibilities" opens them and any
one can be added instead.

Press <kbd>A</kbd> to add or <kbd>I</kbd> to ignore the top card without
reaching for the mouse; the queue can run to dozens of tracks. Decisions are
saved as you go and written to Plex when you press **Update playlist**.

Review choices are remembered against the source's track id across playlists.
Saved matches apply only to the same Plex server and library, because another
server can use the same track id for a different song. Ignore choices apply
across servers. Matches saved by older versions without a server identity need
review once more.

## Settings

The chip in the top right (`Server · Library`) opens the settings panel:

- **Switch server** — move to another Plex server on your account. Available
  when you signed in through plex.tv.
- **Library** — change which music library to match against.
- **Index** — how many tracks are cached, and a rebuild button for when you
  have added music and do not want to wait for the automatic check.
- **Matching** — the auto-accept and review thresholds (see below).
- **Sign out** — forgets the server, its token and the cached library.

## How matching works

Both sides are reduced to a comparable form: `(Official Video)`, `- Remastered
2011`, `(From "Some Film")`, `Artist - Topic` and similar noise is stripped,
guest artists are pulled out of the title, and accents and punctuation are
folded away.

What is deliberately *not* stripped is anything marking a different recording —
`(Live)`, `(Acoustic)`, `(Demo)`, `(… Remix)`. Those become tags, and a
mismatch is penalised hard, because quietly matching a live cut to the studio
version is the usual way a tool like this goes wrong.

Candidates are then scored on title, artist, album and duration:

- **Title** uses a length-sensitive comparison. A containment-based one scores
  `"Sky High"` against `"High"` at 90 and floods the results with wrong songs
  that merely share a word.
- **Artist** carries heavy weight, and a low artist score applies a *ramped
  penalty* on top. Weighting alone cannot sink a wrong match: at 30% weight a
  26/100 artist score still leaves a same-word title above the review floor.
- **Duration** is the disambiguator — it separates the album cut from the
  extended mix when the titles are identical.
- **Placeholder credits** like `Various Artists` count as *no* artist rather
  than a conflicting one, so compilations are not wrongly rejected.

User-curated playlists are full of uploads where the credited artist is really
the uploading channel and the actual artist sits in the title —
`"Steppenwolf - Born To Be Wild"` uploaded by `"Max Shkiv"`, or reversed in
`"Kashmir - Led Zeppelin"`. Both splits are tried and scored against your
library, so whichever reading is real wins on its own merits.

Scores at or above **auto-accept** (default 88) match silently. Below the
**review floor** (default 65) the track is reported missing. In between, you
are asked. Both are adjustable in settings.

## How Spotify is read

Spotify's February 2026 changes moved metadata endpoints off the Client
Credentials flow and put Developer Mode behind a Premium account, which is a
poor trade for reading a public playlist. So this uses what the web player
itself uses, and needs no account or app registration:

1. The public embed page carries a short-lived access token.
2. That token works against Spotify's pathfinder GraphQL endpoint, which pages
   through the entire playlist, 100 tracks at a time, with album names.
3. If that ever stops working, the embed page's own payload still lists the
   first 100 tracks. The playlist is then flagged as truncated, saying how many
   of the total were read, rather than quietly syncing a partial playlist.

That token is scoped to embed playback and is refused by `api.spotify.com`
(`429 QUOTA_EXCEEDED`), which is why the GraphQL endpoint is used instead.

Step 2 depends on a persisted-query hash (`PLAYLIST_QUERY_HASH` in
`playlistamped/spotify.py`) because Spotify refuses raw queries. Spotify
rotates these occasionally; if full playlists stop working, updating that one
constant restores them, and until then step 3 keeps the tool usable.

## Reports

After a run you can download the **full report** as CSV — every track, its
status, what it matched, and the individual component scores — and a **wanted
list** of everything your library does not have, as a plain shopping list.

## Where your data lives

Settings and caches use the standard per-user locations:

| | Path |
|---|---|
| Linux | `~/.config/playlistamped`, `~/.cache/playlistamped` |
| macOS | `~/Library/Application Support/playlistamped` |
| Windows | `%LOCALAPPDATA%\playlistamped` |

`config.toml` holds your Plex token and is written owner-only (`0600`) where
the filesystem supports it. It is never committed, never logged, and never
included in a report. `PLEX_URL`, `PLEX_TOKEN` and `PLEX_SECTION` override the
file if you would rather pass them in.

Signing out deletes the config and the cached library. Review decisions are
kept across sign-out and server switches; saved matches are reused only on
their original server and library.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

They run fully offline against fixtures — no Plex server and no network.

## Limits

Only public playlists. Private ones, and YouTube's Liked Music, need a
logged-in session. For YouTube the plumbing is already there — `YTMusic()` in
`playlistamped/youtube.py` takes an `auth_file`, so wiring up a
`ytmusicapi browser` credential file is all that stands in the way.

Matching can only find what you already own. The tool never downloads music,
and it only ever writes playlists — it does not touch your media files.

## Licence

MIT — see [LICENSE](LICENSE).
