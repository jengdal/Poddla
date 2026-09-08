# Poddla

A self-hosted web app that turns YouTube channels and playlists into audio podcasts.

## What?

- You deploy Poddla on your own server
- You create podcasts in Poddla's web interface
- You subscribe to those podcasts in your podcast player
- Your podcast player lets you know when new episodes appear
- You listen without having to deal with YouTube

## Data use

Poddla only ever fetches data from YT when requested to do so by a client. There are no update
schedules etc. This means no unnecessary resources will be used. Additionally, Poddla minimizes
concurrent requests to YT. If a podcast player downloads two uncached episodes concurrently, they
will be downloaded from YT one after the other.

## Motivation

This is all quite overkill when I could have simply created or vibed a simple python script for
downloading episodes and run it as a cronjob. Actually, there are probably a lot of those on GitHub
already. My motivation for building this app wasn't the app itself. I wanted to experiment with some
tech, set up some patterns for how to do things while finishing the project in a reasonable amount
of time. I have some more interesting ideas I'd like to work on next, and this project will serve as
a starting template for those. That said, it is quite a bit nicer than a cronjob and I'm happy I
created it.

## Security

This applies to most Podcast players, but I'm sure there are exceptions: Poddla's podcast RSS feeds
need to be accessible on the internet for podcast players to use them. Most podcast player apps
don't directly check podcast feeds. They instead offload that work to the app maker's servers.
Having to make the feeds accessible over the internet makes hosting Poddla slightly more serious
than if it would only be available on one's home network.

Using Poddla requires a login that is created on first startup, you'll see mentions of this in
`deploy/docker-compose.yml`. The RSS feed and audio file downloads do not require this login,
because that would not work with podcast players. Instead those two use personal "secret feed
tokens" that are hard to guess and are automatically included in links, you should never have to
manually type them. This is _security by obscurity_, and how private podcast feeds are generally
published. If you expose the RSS feeds on the internet and somebody gets a hold of one of them, they
will be able to access it without any extra login. That applies to the audio download URLs too. I
recommend only exposing the _RSS feed_ on the internet, not the audio downloads or the rest of
Poddla for that matter.

I host my Poddla instance in my homelab and have configured my web server to only allow traffic to
Poddla from my home network, except for the RSS feeds which are open to the internet. I have to be
on my home network or on my VPN to download episodes on my podcast player. Other options include
finding and using a podcast player that fetches feeds directly or running a cronjob that mirrors the
feeds to a separate server/CDN on the internet, but that's not as convenient.

## Why the silly name?

Have you tried naming things? :)

_Podda_ is Swedish slang for "to podcast", and the extra _l_ is there to make it even sillier.

## Installation

### Docker

I have not published a docker image, but you can build one yourself. `devenv` is required for that,
see the Development section for details about `devenv`. If you don't want to deal with Nix and
Devenv on your computer, you can use a VM or create your own `Dockerfile`.

This will build a docker image and load it into your Docker under the name `poddla:latest`.

I've only tested this with Podman, but it should work with Docker as well. See `.env.example` for
what to change if you use Podman or Docker.

```sh
devenv shell
docker-build-load
```

Please see the `deploy/` directory for a sample `docker-compose.yml` file that will run a Poddla
instance on `https://localhost:4444/`. It may serve as a starting point for your own hosting setup.
The sample uses `localhost`, because I don't want to make it too easy to set this up on the
internet. Please read the "Security" section above and protect your deployment.

There's also a command to build and publish docker images (amd64 and arm64) to a registry, I use it
with my forgejo instance.

```sh
devenv shell
docker-publish
```

See `.env.example` for how to configure the registry URL.

## Development

The environment assumes an installation of [Devenv](https://devenv.sh). Devenv sets up the correct
Python, Valkey, dependencies and locks the versions down. This means you get the same version of
everything in the docker image as in your development environment and any other developer's
environment. Devenv is also able to create devcontainers but I haven't tried that yet.

If you don't want to deal with Nix and Devenv on your computer, you can set it up in a VM, or
install everything needed manually.

When you've [Installed Devenv](https://devenv.sh/getting-started/), you can run `devenv shell` in
the root of this repo. It will install all the dependencies and make all devenv scripts available in
your shell.

See the `devenv.nix` file and/or run `devenv info` for details.

### Stack

- Poddla is a [Django](https://www.djangoproject.com) project.
- [Datastar](https://data-star.dev) is used to make the hypermedia driven real-time UI: when
  relevant state changes, new HTML is sent to the browser.
- [Open Props UI](https://open-props-ui.netlify.app/) is used as the basis of the CSS.
- [Valkey](https://valkey.io) with [Valkey-GLIDE](https://glide.valkey.io) is used as a message bus
  and cache.
- [SQLite](https://sqlite.org/) is used as the database, through Django's ORM.
- [Granian](https://github.com/emmett-framework/granian) as the ASGI HTTP2 (h2c) server
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) Is used to interact with YT

### Environment variables

Create your own `.env` file based on `.env.example`.

### Basic commands

Start Valkey and Caddy. This will start process-compose in the active terminal, you'll want to leave
it running.

```sh
devenv up valkey caddy
```

Start `web` in another terminal:

```sh
devenv shell
web
```

Poddla is now available on https://localhost/ .

Django's manage script is available as `manage` inside `devenv shell`.

```sh
devenv shell
manage check
manage makemigrations
# etc.
```

#### Tests

```sh
devenv test
```

### Other

The project uses `uv` to manage python dependencies. While inside the devenv shell, you just use
`uv` normally.

```sh
devenv shell
uv add your-new-dependency
uv sync
# etc.
```

You may notice there's a `package.json` with `vite`, `prettier` and open props (UI). `pnpm` is set
up in `devenv.nix`. At this point the reasons for this are:

- Install and bundle open props and open props UI.
- Prettier for formatting.

```sh
devenv shell
pnpm install
# etc.
```

## LLM use

An LLM has been used for:

- Coming up with ideas for solutions and rubber ducking
- Annoying debugging problems
- The HTML and CSS have been mostly wire-framed up by an LLM according to my instructions
- The tests are mostly LLM generated
- This text has been checked for typos by an LLM

Poddla's features, structure, and technical design decisions are mine.

I don't expect to get any PRs for this project, but if you do want to submit one, I urge you to
understand what you submit.
