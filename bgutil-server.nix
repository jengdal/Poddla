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

  # bgutil runs as TypeScript via Deno — no tsc compile step needed
  dontNpmBuild = true;

  installPhase = ''
    mkdir -p $out
    cp -r src types node_modules package.json deno.json deno.lock tsconfig.json $out/
  '';

  meta = with lib; {
    description = "bgutil PO token provider server for yt-dlp";
    license = licenses.gpl3Only;
  };
}
