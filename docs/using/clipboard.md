# Copy to clipboard

> *"I copy anything we just talked about — a system, a station, coordinates — to your clipboard."*

Elite Dangerous makes you type system names into the galaxy map by hand. COVAS++ can put the name
you just heard straight onto your **Windows clipboard**, so you paste it in instead of typing.

**Example:** *"copy that system to my clipboard"*

## How it works

Just refer to whatever you were talking about:

- *"Where is Elvira Martuuk's system?"* … *"Copy that system to my clipboard."*
- *"Copy that station name."*
- *"Copy those coordinates."*

COVAS++ works out what "that" refers to from the recent conversation and copies the specific value
— usually just a name — then confirms: *"Copied Khun to your clipboard."* Paste it into the galaxy
map search and you're done.

!!! note "This is an explicit copy"
    When you *ask* to copy something, it copies it — even if it happens to be your current system.
    (The galaxy searches have a "you're already there, no need to copy" rule; this direct command
    doesn't, because you asked for it on purpose.)

Most searches (outfitting, ships, systems, stations, and so on) **already copy** their result
system for you automatically. This command is for everything else — any name that came up in
conversation.

If the clipboard can't be written for some reason, COVAS++ tells you out loud rather than crashing
the session. There's nothing to configure — it's always available.
