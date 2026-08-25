# Build a Poddla docker image using the same software we use in the devenv. The nix docker
# tools should be good at producing layered, efficient images.
{
  pkgs,
  lib,
  config,
  inputs,
}:

let
  # Force Linux (host may be Darwin) but keep its arch, so the remote builder
  # builds natively instead of emulating.
  # TODO: Should we do multi-arch here?
  linuxSystem = lib.replaceStrings [ "darwin" ] [ "linux" ] pkgs.stdenv.hostPlatform.system;
  pkgsLinux = import inputs.nixpkgs { system = linuxSystem; };

  bgutil-server-linux = pkgsLinux.callPackage ./bgutil-server.nix { };

  # Use uv2nix to build a venv based on our pyproject.toml and uv.lock.
  # We don't use `languages.python.import` so that we can run this on mac.
  # TODO: Investigate if we don't need to copy `src/` manually below, but
  #       instead rely on it being included from here.
  pythonWorkspace = inputs.uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
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

  # The Poddla sources and static files.
  appSrc = pkgsLinux.runCommand "poddla-src" { } ''
    mkdir -p $out/app
    cp -r ${./src} $out/app/src
    chmod -R u+w $out/app/src

    SECRET_KEY=nix-build-placeholder \
    MEDIA_ROOT=/tmp/build-media \
    DEBUG=False \
    "${app}/bin/python" $out/app/src/manage.py collectstatic --noinput
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
  # Build with `devenv build outputs.poddla-image` (prints the built image's
  # store path as JSON), then pipe it into `docker load`.
  # TODO: Can we use streamLayeredImage instead?
  poddla-image = pkgsLinux.dockerTools.buildLayeredImage {
    name = "poddla";
    tag = "latest";

    contents = [
      app
      appSrc
      poddlaEtc
      pkgsLinux.deno
      pkgsLinux.dockerTools.caCertificates
    ];

    # 1. We need a writeable `$HOME/.cache/` for yt-dlp and deno, we use /tmp for home and make it writeable.
    # 2. The static files are actually in the nix store and symlinked to app/src/staticfiles. We move them
    #    there for real so that they're reachable by other images at their expected path. At the time of
    #    writing Caddy (from the compose file) needs to access them.
    fakeRootCommands = ''
      mkdir -p tmp
      chmod 1777 tmp

      rm -rf app/src/staticfiles
      cp -r --dereference ${appSrc}/app/src/staticfiles app/src/staticfiles
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
}
