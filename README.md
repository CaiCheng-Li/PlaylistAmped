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

## Start here

These instructions start with a computer that does not have Python installed.
You do not need programming experience or Git. Setup uses a few commands;
afterward, you use PlaylistAmped in your browser.

Before starting, you need:

- A Windows, macOS, or Linux computer with an internet connection.
- Access to a Plex Media Server with a music library, including a shared server.
- A public YouTube Music or Spotify playlist link.
- Plexamp installed on the device where you want to listen.

PlaylistAmped matches music already on your Plex server. It does not download
the songs from YouTube or Spotify. Install it on the computer where you want
to manage playlists; it does not need to run on the Plex server itself.

### 1. Install Python

Python runs PlaylistAmped. You need **Python 3.11 or newer**. Follow only the
instructions for your operating system. In command blocks, copy one line at
a time and press **Enter** after each line. Wait for it to finish before
running the next one.

#### Windows

1. Open the [Python downloads page](https://www.python.org/downloads/windows/).
2. Download the **Python install manager**, open the downloaded file, and
   click **Install**.
3. Open the Start menu, type **PowerShell**, and open **Windows PowerShell**.
   If it was already open during installation, close it and open it again.
4. Install Python, then check its version:

   ```powershell
   py install 3.14
   py -3.14 --version
   ```

You should see `Python 3.14.x`, where the last number may vary. If `py` is not
recognized, reopen PowerShell and check the
[official Windows troubleshooting instructions](https://docs.python.org/3/using/windows.html#troubleshooting).

#### macOS

1. Open the [Python downloads page for macOS](https://www.python.org/downloads/macos/).
2. Choose the latest stable Python 3 release and download its **macOS 64-bit
   universal2 installer** (`.pkg`).
3. Open the downloaded installer and follow its prompts.
4. In Finder, open **Applications**, open the new **Python 3.x** folder, and
   double-click **Install Certificates.command**. Let it finish; this sets up
   certificates for secure downloads.
5. Press **Command + Space**, type **Terminal**, and press **Enter**. Check Python:

   ```bash
   python3 --version
   ```

The result must be 3.11 or newer. If you see an older version, close and reopen
Terminal after installation. More details are in the
[official macOS installation guide](https://docs.python.org/3/using/mac.html).

#### Linux

On **Ubuntu or Debian**, open your Terminal application and run:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
python3 --version
```

`sudo` may ask for your computer's password. Nothing appears as you type it;
this is normal. Press Enter when finished, and confirm installation if asked.

The Python version must be **3.11 or newer**. If your distribution provides
an older version, upgrade to a supported distribution release with Python
3.11 or newer before continuing. For other Linux distributions, use their
package manager to install Python 3, pip, and venv support, then run
`python3 --version`. Ubuntu also provides an
[official Python setup guide](https://ubuntu.com/developers/docs/howto/python-setup/).

### 2. Download PlaylistAmped

1. Open the [PlaylistAmped repository](https://github.com/CaiCheng-Li/PlaylistAmped).
2. Click the green **Code** button, then **Download ZIP**.
3. Extract the ZIP: on Windows, right-click it and choose **Extract All**;
   on macOS, double-click it; on Linux, use your archive manager's **Extract** action.
4. Move the extracted `PlaylistAmped-main` folder somewhere you want to keep it,
   such as your Documents folder. Open it and find `README.md` and
   `pyproject.toml`. If you see another `PlaylistAmped-main` folder instead,
   open that inner folder.

Keep this folder after installation. Run the following commands from the
folder that contains `pyproject.toml`, not from inside the ZIP.

### 3. Open a terminal in the project folder

**Windows:** Open that folder in File Explorer, click the address bar, type
`powershell`, and press Enter. A PowerShell window opens in that folder.

**macOS or Linux:** Open Terminal, type `cd` followed by a space, then drag
the extracted project folder into the terminal window and press Enter.
Alternatively, type `cd` followed by the full folder path in quotation marks.
For example, if you saved it in Documents:

```bash
cd "$HOME/Documents/PlaylistAmped-main"
```

Check that you are in the right place: run `dir` on Windows or `ls` on
macOS/Linux. The listing should include `pyproject.toml`.

### 4. Install PlaylistAmped

This creates a `.venv` folder containing the app's Python environment and
installs the packages it needs. Leave the terminal open and run each line
below for your operating system. The download may take a few minutes.

**Windows (PowerShell):**

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

**macOS or Linux:**

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e .
```

Include the final `.` in the last command: it means “install this project
folder.” Wait until the command finishes successfully before continuing.
These commands use the environment directly, so no activation step is needed.
See the [Python packaging guide](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/)
for more about virtual environments.

### 5. Start PlaylistAmped

In the same terminal, run:

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\playlistamped.exe
```

**macOS or Linux:**

```bash
./.venv/bin/playlistamped
```

Your browser should open the app. If it does not, open the address printed
in the terminal, usually <http://127.0.0.1:7391>. If that port is busy, the
app chooses another one, so use the printed address.

Keep the terminal open while using PlaylistAmped. To stop it, return to the
terminal and press **Ctrl+C**. Closing the browser tab alone does not stop it.

### 6. Connect and make your first playlist

1. In the browser, click **Sign in with a code at plex.tv**.
2. Open [plex.tv/link](https://plex.tv/link) in another tab, sign in to the
   Plex account that can access your music server, and enter the displayed code.
3. Return to PlaylistAmped, select your server, choose its music library,
   and click **Done**.
4. Paste a public YouTube Music or Spotify playlist link.
5. For a trial run, open **Options** and select **Preview only**, then click
   **Sync**. The first library scan can take a few minutes.
6. Inspect the results. In **Needs a look**, use **Add** for the suggested
   recording you want or **Ignore** to leave it out.
7. To create the playlist after previewing, clear **Preview only** and click
   **Sync** again. Saved review choices are reused. After any further review
   changes, click **Update playlist** to write them to Plex.
8. Open Plexamp using the same Plex account and server, then find the playlist
   in your playlists view.

You can also connect using a server address and token; see
[Connecting to Plex](#connecting-to-plex) below.

### Opening it again later

Open a terminal in the same project folder (step 3), then run the start
command for your operating system (step 5). You do not need to reinstall
Python or repeat setup. Your Plex connection and review choices are saved.

### Setup troubleshooting

| Problem | What to do |
|---|---|
| `py` or `python3` is not found | Finish step 1 and reopen your terminal. |
| Python is older than 3.11 | Install a newer Python version before creating `.venv`. |
| pip says there is no `pyproject.toml` or the directory is not installable | Return to step 3 and open the extracted folder containing `pyproject.toml`. Include the final `.` in the install command. |
| Linux reports that `venv` or `ensurepip` is unavailable | On Ubuntu/Debian, install `python3-venv`, then repeat step 4. |
| The `.venv` start command is not found | Check the folder in step 3 and make sure all commands in step 4 completed successfully. |
| Downloads fail | Check your internet connection and retry the failed command. On macOS, complete the certificate step in step 1. |
| The browser cannot connect | Keep the app's terminal running and use the exact address it prints. |

For advanced use, the launcher accepts `--port`, `--host`, and `--no-browser`.
It binds to localhost by default. Keep that default for personal use: the
page controls your Plex connection and should not be exposed to the network.

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
