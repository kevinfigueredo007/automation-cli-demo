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

### Pular o sync automático (offline / CI)

```bash
automation release release-example.yaml --no-fetch
```

---

## Fluxo completo de um release

Ao rodar `automation release release-example.yaml --push --tag`, a CLI:

1. **Sync automático** — faz `git fetch origin main dev` e, se a branch local
   estiver apenas atrás do remote, aplica `git pull --ff-only` (avança sem
   criar merge commit). Se a branch local tiver commits que o remote não tem
   (divergência), o pull dessa branch é **pulado** — a CLI nunca cria merge
   commit silenciosamente.

2. **Validação pre-flight** — checa que `main` e `dev` existem, que
   `release/<version>` ainda não existe, que a working tree está limpa, e que
   os paths do manifesto existem em `dev` (ou configuram remoção total).

3. **Construção da release** — cria `release/<version>` a partir de `main`,
   sobrepõe os paths selecionados de `dev` (incluindo adições, modificações e
   deleções), e commita com a mensagem `release: <version>`.

4. **Tag** (com `--tag`) — cria uma tag `<version>` apontando para o commit da
   release.

5. **Push** (com `--push`) — empurra a branch de release (e a tag, se
   `--tag`) para o `origin` com `git push -u origin release/<version>`.

6. **Volta para `dev`** — após o push bem-sucedido, faz `git checkout dev`
   (ou a branch configurada em `--source`).

### O que acontece se algo der errado

| Situação | Comportamento |
|---|---|
| `fetch`/`pull` falha (rede/remote) | sync é pulado, release prossegue |
| Branch local divergiu do remote | sync daquela branch é pulado (aviso) |
| Push falha (non-fast-forward) | fica na branch de release, **não** volta para `dev` |
| Branch de release já existe | aborta antes de criar qualquer coisa |
| Working tree suja | aborta antes de criar qualquer coisa |
| Tag já existe (com `--tag`) | aborta antes de criar qualquer coisa |

### Flags do comando `release`

| Flag | Descrição |
|---|---|
| `--dry-run` | Computa e mostra o que aconteceria; não altera nada |
| `--tag` | Cria uma tag `<version>` além da branch |
| `--push` | Empurra a branch (e tag) para o remote e volta para `--source` |
| `--no-fetch` | Pula o sync automático de `main`/`dev` |
| `--base <branch>` | Branch base (default: `main`) |
| `--source <branch>` | Branch de origem (default: `dev`) |
| `--remote <name>` | Remote para push (default: `origin`) |

### Exemplo de saída

```
Branch sync:
  pulled  main (+2 commit(s))
  skipped dev (already up to date)
Created release branch: release/1.0.0
  base:    main
  source:  dev
  paths:   3 selected
Changes:
  1 files added
  3 files modified
  1 files deleted
Created tag: 1.0.0
Pushed release/1.0.0 to origin
Pushed tag 1.0.0 to origin
Switched back to dev
```

---

## Manifesto

Arquivo YAML declarando a versão e os paths a promover:

```yaml
version: "1.0.0"

paths:
  - playbooks/aws/restart-instance/
  - roles/aws_restart/
  - roles/aws_common/
```

Regras:
- `version` deve ser SemVer válido (`MAJOR.MINOR.PATCH`);
- `paths` não vazio, único, relativo à raiz do repo;
- paths absolutos e `..` (traversal) são rejeitados;
- paths sobrepostos (ex: `roles/aws/` + `roles/aws/restart/`) são normalizados
  automaticamente.

Veja mais detalhes no código em `src/automation_cli/`.
