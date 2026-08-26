{
  lib,
  buildNpmPackage,
  fetchFromGitHub,
  deno,
}:

buildNpmPackage {
  pname = "bgutil-server";
  version = "1.3.2";

  src =
    (fetchFromGitHub {
      owner = "Brainicism";
      repo = "bgutil-ytdlp-pot-provider";
      rev = "1.3.2";
      hash = "sha256-vlhuw0Ci/xfPgLxjeW7E+Pz9Fo6yeME3cyVRf8NAAPU=";
    })
    + "/server";

  # sha256 of all npm dep tarballs — fill in after first failed build triggers hash mismatch
  npmDepsHash = "sha256-hpXVvhJm66+ETJdGAbEa/QZ4rxOYBD8RJqSItlNpoOg=";

  # Skip canvas's node-gyp build (canvas is a jsdom optional dep, never imported in source)
  npmFlags = [ "--ignore-scripts" ];

  # devDependencies (eslint, typescript, prettier, swc-node, ...) are only used for
  # linting/compiling — irrelevant here since dontNpmBuild = true means we never
  # compile or lint, we just run the TypeScript source directly via Deno.
  npmInstallFlags = [ "--omit=dev" ];

  # bgutil runs as TypeScript via Deno — no tsc compile step needed
  dontNpmBuild = true;

  # Everything here is imported as a library by Deno, never invoked as a standalone CLI. But
  # `npm install` (via buildNpmPackage's own install hook, not stdenv's fixupPhase — so
  # dontPatchShebangs doesn't reach it) rewrites a handful of unrelated deps' bin scripts to an
  # absolute nodejs shebang regardless of --ignore-scripts. That ends up the *only* nix reference
  # this derivation has, which drags a whole nodejs+icu4c+gtest closure into the production image
  # (docker.nix pulls in whatever bgutil-server references via BGUTIL_SERVER_HOME) for scripts
  # nothing calls. Drop the unused node_modules/.bin shims and neutralize any leftover absolute
  # nodejs shebangs on the underlying files (e.g. node-addon-api/tools/conversion.js, which isn't
  # even exposed via .bin) so no unused nodejs reference survives.
  dontPatchShebangs = true;

  installPhase = ''
    mkdir -p $out
    cp -r src types node_modules package.json deno.json deno.lock tsconfig.json $out/
    rm -rf $out/node_modules/.bin
    grep -rl '^#!.*/bin/node$' $out/node_modules 2>/dev/null \
      | xargs -r sed -i '1s|^#!.*/bin/node$|#!/usr/bin/env node|'
  '';

  meta = with lib; {
    description = "bgutil PO token provider server for yt-dlp";
    license = licenses.gpl3Only;
  };
}
