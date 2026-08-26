# Build a Poddla docker image using the same software we use in the devenv. The nix docker
# tools should be good at producing layered, efficient images.
{
  pkgs,
  lib,
  config,
  inputs,
}:

let
  # Build our image for the given linux architecture:
  # To use this you need a builder set up that support the architecture(s). I use rosetta-builder
  # on my mac but you could build each image on a computer of the same architecture.
  mkImage =
    linuxSystem:
    let
      pkgsLinux = import inputs.nixpkgs { system = linuxSystem; };

      bgutil-server-linux = pkgsLinux.callPackage ./bgutil-server.nix { };

      # Use uv2nix to build a venv based on our pyproject.toml and uv.lock.
      # We don't use `languages.python.import` so that we can run this on mac.
      # TODO: Investigate if we don't need to copy `src/` manually below, but
      #       instead rely on it being included from here.
      pythonWorkspace = inputs.uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ../.; };
      pythonOverlay = pythonWorkspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      pythonSet =
        (pkgsLinux.callPackage inputs.pyproject-nix.build.packages {
          python = inputs.nixpkgs-python.packages.${linuxSystem}.${config.languages.python.version};
        }).overrideScope
          (
            lib.composeManyExtensions [
              inputs.pyproject-build-systems.overlays.default
              pythonOverlay
            ]
          );
      app = pythonSet.mkVirtualEnv "poddla-env" pythonWorkspace.deps.default;

      # The Poddla sources.
      appSrc = pkgsLinux.runCommand "poddla-src" { } ''
        mkdir -p $out/app
        cp -r ${../src} $out/app/src
        chmod -R u+w $out/app/src
      '';

      # Collect the static files separately.
      collectedStatic = pkgsLinux.runCommand "poddla-staticfiles" { } ''
        mkdir -p build/app/src
        cp -r ${appSrc}/app/src/. build/app/src/
        chmod -R u+w build/app/src

        SECRET_KEY=nix-build-placeholder \
        MEDIA_ROOT=/tmp/build-media \
        DEBUG=False \
        "${app}/bin/python" build/app/src/manage.py collectstatic --noinput

        mv build/app/src/staticfiles $out
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
        "${app}/bin/python" src/manage.py migrate --noinput
        exec "${app}/bin/uvicorn" --app-dir src --port 8000 --host 0.0.0.0 \
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
          appSrc
          poddlaEtc
          pkgsLinux.deno
          pkgsLinux.dockerTools.caCertificates
        ];

        # 1. We need a writeable `$HOME/.cache/` for yt-dlp and deno, we use /tmp for home and make it writeable.
        # 2. app/src/staticfiles would otherwise be a symlink into the nix store. We replace it with a real
        #    copy of collectedStatic so that it's reachable by other images at its expected path. At the time
        #    of writing Caddy (from the compose file) needs to access it, via a shared docker volume.
        fakeRootCommands = ''
          mkdir -p tmp
          chmod 1777 tmp

          rm -rf app/src/staticfiles
          cp -r --dereference ${collectedStatic} app/src/staticfiles
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
