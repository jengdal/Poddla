- [ ] Look into running bgutil-server as a server instead of as a script. The deno dependency in the
      poddler docker image is huge, and the server would probably perform better than the script.
      There is a bgutil-server docker image we could use in the compose file.
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider
- [ ] HTTP2: Use granian or another h2c capable server instead of uvicorn. I don't think the
      http1.1<->http2 security problems are a big problem for _this_ project, but it would serve as
      an example for future projects. https://http1mustdie.com
- [ ] Include chapter markers based on YT data, and/or use a local LLM to figure them out based on
      the audio.
