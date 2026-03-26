{
  description = "Flake setup for RRSS aka RingRSS";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = {
    nixpkgs,
    self,
    ...
  }: let
    systems = ["x86_64-linux" "aarch64-linux" "aarch64-darwin"];
    forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
  in {
    devShells = forAllSystems (system: let
      pkgs = import nixpkgs {
        inherit system;
        config = {
          permittedInsecurePackages = ["olm-3.2.16"];
        };
      };

      pythonEnv = pkgs.python312.withPackages (ps:
        with ps; [
          requests
          matrix-nio

          # Encryption dep
          python-olm

          # Encryption dep deps
          cachetools
          atomicwrites
          peewee
        ]);
    in {
      default = pkgs.mkShell {
        packages = [
          pythonEnv
          pkgs.sqlite

          # Encryption dep
          pkgs.olm
        ];

        shellHook = ''
          export XDG_STATE_HOME="''${XDG_STATE_HOME:-$HOME/.local/state}"
          export RRSS_STATE_DIR="$XDG_STATE_HOME/rrss"
          mkdir -p "$RRSS_STATE_DIR"

          # (dev env only)
          if [ -f .env ]; then
            set -a
            source .env
            set +a
          fi
        '';
      };
    });

    nixosModules.rrss = {
      config,
      lib,
      pkgs,
      ...
    }: let
      cfg = config.services.rrss;

      pythonEnv = pkgs.python312.withPackages (ps:
        with ps; [
          requests
          matrix-nio
          python-olm
          cachetools
          atomicwrites
          peewee
        ]);
    in
      with lib; {
        options.services.rrss = {
          enable = mkEnableOption "RRSS RSS notification service";

          environmentFile = mkOption {
            type = types.path;
            description = ''
              Path to a file containing environment variables (one KEY=value per line).
              Must include: MATRIX_HOMESERVER, MATRIX_USER_ID, MATRIX_PASSWORD, MATRIX_ROOM_ID.
              Example path: /run/secrets/rrss-env
            '';
          };

          stateDir = mkOption {
            type = types.str;
            default = "/var/lib/rrss";
            description = "Directory for the SQLite database, Matrix credential cache, and E2E store.";
          };

          user = mkOption {
            type = types.str;
            default = "rrss";
          };

          group = mkOption {
            type = types.str;
            default = "rrss";
          };
        };

        config = mkIf cfg.enable {
          users.users.${cfg.user} = {
            isSystemUser = true;
            group = cfg.group;
            home = cfg.stateDir;
            createHome = true;
            description = "RRSS service user";
          };

          users.groups.${cfg.group} = {};

          systemd.services.rrss = {
            description = "RRSS RSS-to-Sink notification daemon";
            wantedBy = ["multi-user.target"];
            after = ["network-online.target"];
            requires = ["network-online.target"];

            serviceConfig = {
              User = cfg.user;
              Group = cfg.group;
              WorkingDirectory = cfg.stateDir;

              # Secrets are injected here, systemd reads the file and
              # puts each KEY=value into the process environment.
              EnvironmentFile = cfg.environmentFile;

              Environment = [
                "RRSS_STATE_DIR=${cfg.stateDir}"
              ];

              ExecStart = "${pythonEnv}/bin/python ${self}src/main.py";

              Restart = "on-failure";
              RestartSec = "10s";

              PrivateTmp = true;
              NoNewPrivileges = true;
              ProtectSystem = "strict";
              ReadWritePaths = [cfg.stateDir];
            };
          };
        };
      };
  };
}
