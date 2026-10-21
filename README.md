# Boca a Oreja

**Low-cost simultaneous interpretation using the smartphones people already carry.**

*Boca a oreja* is Spanish for "mouth to ear". The project replaces the interpretation
booths, radio transmitters and rented headsets of a typical conference setup with a
small hub in the room and an app on everyone's phone.

> **Status: Phase 0, feasibility testing.** There is no product code yet. This
> repository holds the development plan ([docs/PLAN.md](docs/PLAN.md)) and the
> tooling for the Phase 0 tests ([phase0/](phase0/README.md)).

## How it works

A **hub** — a Raspberry Pi or any small computer with Wi-Fi — sits in the room. It
creates its own Wi-Fi network, takes the speaker's audio from the venue's PA system,
and routes audio between the phones connected to it. No Internet connection is needed
during an event.

```
             PA system
                │ floor audio
                ▼
   ┌──────────── Hub ────────────┐
   │  Wi-Fi access point         │
   │  audio routing and mixing   │
   └──┬───────────▲───────────┬──┘
      │ floor     │ language  │ language
      ▼           │ channel   ▼ channel
 Interpreters ────┘        Audience
```

1. Audience members join the hub's Wi-Fi, often by scanning a QR code in the room,
   and pick a language.
2. Interpreters hear the floor audio in their headset and speak their interpretation
   into the app. Each language is its own channel.
3. Each listener hears the channel for the language they chose, on their own phone and
   earphones.
4. When someone in the audience asks a question, their phone's microphone is routed
   into the floor audio, so the room hears it and the interpreters interpret it.

## Roles

| Role          | What they do                                                          |
|---------------|-----------------------------------------------------------------------|
| Administrator | Sets up the hub: Wi-Fi, rooms and sessions, languages, audio input    |
| Interpreter   | Listens to the floor audio and interprets into a target language      |
| Audience      | Chooses a language and listens to it                                  |
| Questioner    | An audience member whose microphone is fed into the floor audio       |

## Planned features

- Runs fully offline on inexpensive hardware.
- Apps for **Android and iOS**.
- Several interpreters per language, with handover between them.
- Multi-room, multi-session events, prepared in advance, with every hub carrying the
  whole event's configuration.
- QR codes for joining a room, a language or an admin session.
- Automatic upgrade downloads when the hub is online, installed automatically or by
  an administrator.

## Proposed technology

These are the current proposals from the plan, not final decisions:

| Part         | Proposal                                                              |
|--------------|-----------------------------------------------------------------------|
| Hub hardware | Raspberry Pi 4/5 or similar, USB audio interface for PA in and out    |
| Wi-Fi        | hostapd + dnsmasq; a USB adapter or external AP for larger audiences  |
| Audio        | WebRTC with Opus, via a LiveKit server on the hub, for low latency    |
| Mixing       | GStreamer or PipeWire for floor audio plus questioner                 |
| Apps         | Flutter, one codebase for Android and iOS                             |
| Hub service  | Go (or Python for prototyping)                                        |

The first phase is a set of feasibility tests — Wi-Fi client capacity, end-to-end
latency, and how phones behave on a Wi-Fi network without Internet — before any
product code is written. The full phased plan, open decisions and risks are in
[docs/PLAN.md](docs/PLAN.md).

## Repository contents

| Path                            | Contents                                       |
|---------------------------------|------------------------------------------------|
| `docs/PLAN.md`, `docs/PLAN.pdf` | Development plan: architecture, phases, risks  |
| `phase0/`                       | Tooling for the Phase 0 feasibility tests      |

## License

[MIT](LICENSE) © 2026 Eddie Bassey Olsson
