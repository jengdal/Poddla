{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

{
  process.manager.implementation = "process-compose";

  # https://devenv.sh/packages/
  packages = [
    pkgs.git
    pkgs.nil
    pkgs.statix
    pkgs.nixfmt
    pkgs.valkey

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
      uv run uvicorn --port 8000 --timeout-graceful-shutdown 0 --reload --reload-include *.css \
              --reload-include *.js --reload-include *.html youtube_to_podcast.asgi:application
    '';
  };

  processes.web = {
    exec = ''
      web
    '';
  };

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
  '';

  # Avoid building the python package from sources:
  cachix.pull = [ "nixpkgs-python" ];

  # See full reference at https://devenv.sh/reference/options/
}
