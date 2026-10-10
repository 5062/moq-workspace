{
  description = "Pinned MoQ trace toolkit, instrumented relays, and transport forks";

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
      # Only what the workspace justfile runs itself. Each component builds in
      # its own development shell.
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.git
            pkgs.just
          ];
        };
      });
    };
}
