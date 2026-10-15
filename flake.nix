{
  description = "Pinned MoQ trace toolkit, instrumented relays, and experiment runner";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # Python tooling and its native decoder run here. Instrumented components
      # retain their own development shells.
      devShells = forAll (
        pkgs:
        let
          python = pkgs.python3.withPackages (
            packages: with packages; [
              asyncssh
              babeltrace2
              cryptography
              dpkt
              duckdb
              matplotlib
              numpy
              pyarrow
              pydantic
              build
              setuptools
              setuptools-rust
            ]
          );
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.git
              pkgs.just
              pkgs.lttng-tools
              pkgs.lttng-ust
              pkgs.util-linux
              pkgs.ruff
              pkgs.rustc
              pkgs.cargo
              pkgs.rustfmt
              pkgs.clippy
              pkgs.babeltrace2
              pkgs.clang
              pkgs.pkg-config
              pkgs.rustPlatform.bindgenHook
              python
              (pkgs.writeShellScriptBin "moq-workspace-bench" ''
                exec ${python}/bin/python -P -m moq_workspace_bench.cli "$@"
              '')
              (pkgs.writeShellScriptBin "moq-trace" ''
                exec ${python}/bin/python -P -m moq_trace.run.cli "$@"
              '')
            ];
            hardeningDisable = [ "fortify" ];
            shellHook = ''
              moq_workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
              if [ ! -d "$moq_workspace_root/bench/src/moq_workspace_bench" ]; then
                moq_workspace_root="$(git rev-parse --show-superproject-working-tree)"
              fi
              export PYTHONPATH="$moq_workspace_root/bench/src:$moq_workspace_root/python/src''${PYTHONPATH:+:$PYTHONPATH}"
              unset moq_workspace_root
            '';
          };
        }
      );
    };
}
