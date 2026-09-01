# Build a Poddla docker image using the same software we use in the devenv. The nix docker
# tools are good at producing layered, efficient images. It produces one layer per nix
# derivation. Even the python packages, including poddla itself gets their own layers.
{
  pkgs,
  lib,
  inputs,
  pythonPackageName,
}:

let
  # We don't use `languages.python.import` so that we can run this on mac.
  # Use uv2nix to build a venv based on our pyproject.toml and uv.lock.
  # This just parses uv.lock and pyproject.toml, it can be shared across the two architectures:
  pythonWorkspace = inputs.uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ../.; };
  pythonOverlay = pythonWorkspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

  # Build our image for the given linux architecture:
  # To use this you need a builder set up that support the architecture(s). I use rosetta-builder
  # on my mac but you could build each image on a computer of the same architecture.
  mkImage =
    linuxSystem:
    let
      pkgsLinux = import inputs.nixpkgs { system = linuxSystem; };

      bgutil-server-linux = pkgsLinux.callPackage ./bgutil-server.nix { };

      pythonSet =
        (pkgsLinux.callPackage inputs.pyproject-nix.build.packages {
          python = pkgsLinux.${pythonPackageName};
        }).overrideScope
          (
            lib.composeManyExtensions [
              inputs.pyproject-build-systems.overlays.default
              pythonOverlay
            ]
          );
      app = pythonSet.mkVirtualEnv "poddla-env" pythonWorkspace.deps.default;

      # Collect the static files separately.
      collectedStatic = pkgsLinux.runCommand "poddla-staticfiles" { } ''
        SECRET_KEY=nix-build-placeholder \
        MEDIA_ROOT=/tmp/build-media \
        STATIC_ROOT=$out \
        DJANGO_SETTINGS_MODULE=poddla.settings \
        DEBUG=False \
        "${app}/bin/django-admin" collectstatic --noinput
      '';

      # uid 1000 -> "poddla", so the non-root container user resolves to a real user.
      poddlaEtc = pkgsLinux.runCommand "poddla-etc" { } ''
        mkdir -p $out/etc
        echo "root:x:0:0:root:/root:/bin/sh" > $out/etc/passwd
        echo "poddla:x:1000:1000::/app:/bin/sh" >> $out/etc/passwd
        echo "root:x:0:" > $out/etc/group
        echo "poddla:x:1000:" >> $out/etc/group
      '';

      poddlaProdEntrypoint = pkgsLinux.writeShellScriptBin "poddla-prod-entrypoint" ''
        set -euo pipefail
        "${app}/bin/django-admin" migrate --noinput
        "${app}/bin/django-admin" create_initial_admin
        exec "${app}/bin/uvicorn" --port 8000 --host 0.0.0.0 \
                --timeout-graceful-shutdown 0 \
                poddla.asgi:application
      '';
    in
    {
      inherit app;

      # A minimal, non-root Docker image running a prod version of `web`.
      # Build with `devenv build outputs.poddla-image-<amd64|arm64>` (prints the built image's
      # store path as JSON), then pipe it into `docker load`.
      # TODO: Can we use streamLayeredImage instead?
      poddla-image = pkgsLinux.dockerTools.buildLayeredImage {
        name = "poddla";
        tag = "latest";
        maxLayers = 127;

        contents = [
          app
          poddlaEtc
          pkgsLinux.deno
          pkgsLinux.dockerTools.caCertificates
        ];

        # 1. We need a writeable `$HOME/.cache/` for yt-dlp and deno, we use /tmp for home and make it writeable.
        # 2. /staticfiles is where Caddy (from the compose file) expects to find static files, via a shared
        #    docker volume. We put a real copy of collectedStatic there - a symlink into the nix store
        #    wouldn't be reachable from other images.
        fakeRootCommands = ''
          mkdir -p tmp
          chmod 1777 tmp

          # WorkingDir below, otherwise never created on disk.
          mkdir -p app

          cp -r --dereference ${collectedStatic} staticfiles
        '';

        config = {
          Entrypoint = [ "${poddlaProdEntrypoint}/bin/poddla-prod-entrypoint" ];
          WorkingDir = "/app";
          User = "1000:1000";
          Env = [
            "PATH=/bin"
            "SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt"
            "HOME=/tmp"
            "BGUTIL_SERVER_HOME=${bgutil-server-linux}"
            "DJANGO_SETTINGS_MODULE=poddla.settings"
          ];
        };
      };
    };

  images = {
    amd64 = mkImage "x86_64-linux";
    arm64 = mkImage "aarch64-linux";
  };
in
{
  poddla-image-amd64 = images.amd64.poddla-image;
  poddla-image-arm64 = images.arm64.poddla-image;
}
