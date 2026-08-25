# automation-cli-demo

Monorepo de automações Ansible/AAP com CLI de release seletiva.

## Estrutura

```
playbooks/aws/        - automações AWS (restart, snapshot)
playbooks/telecom/    - automações Telecom (bgp, dns)
roles/aws_restart/   - role de restart de instâncias EC2
roles/aws_common/     - defaults e tasks comuns de AWS
collections/          - requirements de collections
automation-cli/       - CLI de release seletiva (src/ + tests/)
```

## Branches

- `main`  -> ambiente PROD (AAP PROD)
- `stage` -> ambiente STAGE (AAP STAGE)
- `dev`   -> ambiente DEV (AAP DEV)

Veja o README da CLI em [`automation-cli/README.md`](automation-cli/README.md).
