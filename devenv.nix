{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

let
  bgutil-server = pkgs.callPackage ./nix/bgutil-server.nix { };

  docker = import ./nix/docker.nix {
    inherit
      pkgs
      lib
      config
      inputs
      ;
  };
in

{
  dotenv.enable = false;
  dotenv.disableHint = true;

  process.manager.implementation = "process-compose";

  # https://devenv.sh/packages/
  packages = [
    pkgs.git
    pkgs.nil
    pkgs.statix
    pkgs.nixfmt
    pkgs.valkey
    pkgs.jq

    # For yt-dlp:
    pkgs.deno
  ];

  # https://devenv.sh/languages/
  languages = {
    python = {
      enable = true;
      version = "3.14";
      lsp.package = pkgs.basedpyright;
      uv = {
        enable = true;
        package = pkgs.uv;

        sync = {
          enable = true;
          allGroups = true;
        };
      };
    };
  };

  languages = {
    # typescript.enable = true;
    javascript = {
      enable = true;

      pnpm = {
        enable = true;
        install.enable = true;
      };
    };
  };

  scripts.manage.exec = ''
    uv run src/manage.py "$@"
  '';

  scripts.web = {
    exec = ''
      if [ -f .env ]; then
        set -a
        source .env
        set +a
      fi
      uv run uvicorn --port 8000 --host 0.0.0.0 --timeout-graceful-shutdown 0 \
              --reload --reload-dir src \
              --reload-include '**/*.html' \
              --reload-include '**/*.css' \
              --reload-include '**/*.js' \
              poddla.asgi:application
    '';
  };

  scripts.run_bandit = {
    exec = ''
      uv run bandit -r src/
    '';
  };

  # Build both arch images (see `docker.nix`) and publish them to the registry as one
  # multi-arch tag. Usage: `devenv shell docker-publish [tag]` (defaults to `latest`).
  # Reads DOCKER_REGISTRY_IMAGE from .env (see .env.example). Assumes you've already
  # run `docker login forgejo.example.com`.
  scripts.docker-publish = {
    exec = ''
      set -euo pipefail

      if [ -f .env ]; then
        set -a
        source .env
        set +a
      fi

      REGISTRY_IMAGE="''${DOCKER_REGISTRY_IMAGE:?DOCKER_REGISTRY_IMAGE is not set (see .env.example)}"
      TAG="''${1:-latest}"

      publish_arch() {
        local arch="$1" output="$2"
        echo "Building $arch image..."
        local store_path
        store_path=$(devenv build "outputs.$output" | jq -r ".\"outputs.$output\"")
        docker load < "$store_path"
        docker tag poddla:latest "$REGISTRY_IMAGE:$TAG-$arch"
        docker push "$REGISTRY_IMAGE:$TAG-$arch"
      }

      publish_arch amd64 poddla-image-amd64
      publish_arch arm64 poddla-image-arm64

      docker manifest create "$REGISTRY_IMAGE:$TAG" \
        --amend "$REGISTRY_IMAGE:$TAG-amd64" \
        --amend "$REGISTRY_IMAGE:$TAG-arm64"
      docker manifest push "$REGISTRY_IMAGE:$TAG"
    '';
  };

  # Run with `devenv test`.
  # devenv will start the needed processes.
  enterTest = ''
    if [ -f .env ]; then
      set -a
      source .env
      set +a
    fi
    wait_for_port "$VALKEY_PORT"
    manage test -v 2
  '';

  processes.web = {
    exec = ''
      web
    '';
  };

  env.BGUTIL_SERVER_HOME = "${bgutil-server}";

  processes.valkey = {
    exec = ''
      if [ -f .env ]; then
        set -a
        source .env
        set +a
      fi
      ${pkgs.valkey}/bin/valkey-server --port $VALKEY_PORT
    '';

    watch = {
      paths = [
        ./.env
      ];
    };
  };

  enterShell = ''
    if [ -f .env ]; then
      set -a
      source .env
      set +a
    fi
    source .devenv/state/venv/bin/activate

    # Check bgutil Python plugin version matches the Nix-packaged script version.
    if [ -n "$BGUTIL_SERVER_HOME" ]; then
      _bgutil_py_ver=$(python -c "from yt_dlp_plugins.extractor.getpot_bgutil import __version__; print(__version__)" 2>/dev/null)
      _bgutil_nix_ver=$(python -c "import json; print(json.load(open('$BGUTIL_SERVER_HOME/package.json'))['version'])" 2>/dev/null)
      if [ "$_bgutil_py_ver" != "$_bgutil_nix_ver" ]; then
        echo "WARNING: bgutil version mismatch — Python plugin: $_bgutil_py_ver, Nix script: $_bgutil_nix_ver" >&2
        echo "         Update both bgutil-ytdlp-pot-provider in pyproject.toml and the version in bgutil-server.nix to the same version." >&2
      fi
      unset _bgutil_py_ver _bgutil_nix_ver
    fi
  '';

  # Avoid building the python package from sources:
  cachix.pull = [ "nixpkgs-python" ];

  # Build a docker image, see `docker.nix`.
  outputs = {
    inherit (docker) poddla-image-amd64 poddla-image-arm64;
  };

  # See full reference at https://devenv.sh/reference/options/
}
