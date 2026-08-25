{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

let
  bgutil-server = pkgs.callPackage ./nix/bgutil-server.nix { };

  docker = import ./docker.nix {
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

  # See full reference at https://devenv.sh/reference/options/
}
