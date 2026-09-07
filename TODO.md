- [ ] Look into running bgutil-server as a server instead of as a script. The deno dependency in the
      poddler docker image is huge, and the server would probably perform better than the script.
      There is a bgutil-server docker image we could use in the compose file.
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider
- [ ] Include chapter markers based on YT data, and/or use a local LLM to figure them out based on
      the audio.
- [ ] Let the admin user manage users?

- The episode list page
  - [ ] Indicate which episodes are downloaded
  - [ ] Let the user download episodes

