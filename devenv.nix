{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

let
  pythonPackageName = "python314";

  bgutil-server = pkgs.callPackage ./nix/bgutil-server.nix { };

  docker = import ./nix/docker.nix {
    inherit
      pkgs
      lib
      inputs
      pythonPackageName
      ;
  };
in

{
  # `dotenv` is deprecated and doesn't work with my flake based install anyway.
  # TODO: Consider secretspec instead?
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
    pkgs.caddy

    # For yt-dlp:
    pkgs.deno
  ];

  # https://devenv.sh/languages/
  languages = {
    python = {
      enable = true;
      package = pkgs.${pythonPackageName};
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
    javascript = {
      # Atm. pnpm is only used for installing the open props UI _CSS_ package.
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

      # Configure Granian using the .env file.
      uv run granian \
        --process-name poddla \
        --interface asgi \
        --workers 1 \
        --no-ws \
        --workers-kill-timeout 1 \
        --reload \
        --reload-paths ./src/ \
        poddla.asgi:application
    '';
  };

  scripts.run_bandit = {
    exec = ''
      uv run bandit -c pyproject.toml -r src/
    '';
  };

  # Wraps docstrings to 100 columns (see [tool.docformatter] in pyproject.toml).
  # Plain `#` comments are not reflowed by any tool — ruff's W505 only flags them.
  scripts.format_docstrings = {
    exec = ''
      uv run docformatter --in-place src/
    '';
  };

  # Create a HTML coverage report based on the `.coverage` file produced by the previous
  # `devenv test` run:
  scripts.coverage_html = {
    exec = ''
      uv run coverage html
      echo "Coverage report: htmlcov/index.html"
      open htmlcov/index.html
    '';
  };

  # This builds the docker image and loads it into your docker. Use it with: `poddla:latest`.
  scripts.docker-build-load = {
    exec = ''
      set -euo pipefail

      output="poddla-image-$(uname -m)"
      echo "Building $output..."
      store_path=$(devenv build "outputs.$output" | jq -r ".\"outputs.$output\"")
      docker load < "$store_path"
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

      # Built the two architectures in one go. The slow uv2nix parsing can then be shared between them.
      echo "Building amd64 and arm64 images..."
      build_paths=$(devenv build outputs.poddla-image-amd64 outputs.poddla-image-arm64)

      publish_arch() {
        local arch="$1" output="$2"
        local store_path
        store_path=$(jq -r ".\"outputs.$output\"" <<< "$build_paths")
        docker load < "$store_path"
        docker tag poddla:latest "$REGISTRY_IMAGE:$TAG-$arch"
        docker push "$REGISTRY_IMAGE:$TAG-$arch"
      }

      publish_arch amd64 poddla-image-amd64
      publish_arch arm64 poddla-image-arm64

      # Remove any items from previous runs.
      # TODO: Could we be doing these things better?
      docker manifest rm "$REGISTRY_IMAGE:$TAG" 2>/dev/null || true

      docker manifest create "$REGISTRY_IMAGE:$TAG" \
        --amend "$REGISTRY_IMAGE:$TAG-amd64" \
        --amend "$REGISTRY_IMAGE:$TAG-arm64"
      docker manifest push "$REGISTRY_IMAGE:$TAG"
    '';
  };

  # Run with `devenv test`.
  # devenv will start the needed processes.
  # Note that coverage is configured in pyproject.toml.
  enterTest = ''
    set -euo pipefail

    if [ -f .env ]; then
      set -a
      source .env
      set +a
    fi
    wait_for_port "$VALKEY_PORT"
    uv run coverage run src/manage.py test -v 2
    uv run coverage report -m
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

  services.caddy = {
    enable = true;
    config = ''
      https://localhost:443 {
        reverse_proxy h2c://127.0.0.1:8000
      }
    '';
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

  # Build a docker image, see `docker.nix`.
  outputs = {
    inherit (docker) poddla-image-amd64 poddla-image-arm64;
  };

  # See full reference at https://devenv.sh/reference/options/
}
