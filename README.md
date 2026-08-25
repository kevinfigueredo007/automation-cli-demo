# automation-cli-demo

Monorepo de automações Ansible/AAP com CLI de release seletiva.

## Estrutura

```
playbooks/aws/        - automações AWS (restart, snapshot, sg-audit)
playbooks/telecom/    - automações Telecom (bgp, dns)
roles/aws_restart/    - role de restart de instâncias EC2
roles/aws_common/     - defaults e tasks comuns de AWS
collections/          - requirements de collections
automation-cli/       - CLI de release seletiva (src/ + tests/)
```

## Branches

- `main`  -> ambiente PROD (AAP PROD)
- `stage` -> ambiente STAGE (AAP STAGE)
- `dev`   -> ambiente DEV (AAP DEV)

## CLI de release

A CLI em `automation-cli/` cria releases seletivas a partir de um manifesto
YAML. Instale com:

```bash
pip install -e .
```

### Validar um manifesto

```bash
automation validate release-example.yaml
```

### Dry run

```bash
automation release release-example.yaml --dry-run
```

### Criar a branch de release

```bash
automation release release-example.yaml
```

### Criar e taguear

```bash
automation release release-example.yaml --tag
```

### Criar, taguear, empurrar e voltar para `dev`

```bash
automation release release-example.yaml --push --tag
```

Com `--push`:

1. cria `release/<version>` a partir de `main`;
2. aplica os paths selecionados de `dev`;
3. commita (e cria a tag se `--tag`);
4. `git push -u origin release/<version>` (e empurra a tag se `--tag`);
5. `git checkout dev` (ou a branch configurada em `--source`).

Se o push falhar, a CLI **não** volta para `dev` — você permanece na branch
de release para inspecionar e tentar o push manualmente.

Veja o README completo da CLI em [`automation-cli/README.md`](automation-cli/README.md)
(se presente) ou no código em `src/automation_cli/`.
