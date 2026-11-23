# Publicador do Instagram · soaone Academy

Um container pequeno na VPS lê `calendario.json` a cada 5 minutos e publica no @academy.soaone, pela API oficial do Instagram, os posts cuja hora chegou. As imagens ficam em `site/ig/` e o nginx do portal as serve em `https://academy.soaone.com.br/ig/`, que é de onde a API do Instagram as baixa.

O que ele faz sozinho:

- publica carrossel, imagem, reel e story, na ordem e com a legenda de `legendas/`;
- **respeita a liberação dos módulos**: post com `"modulo": N` só sai se o módulo N estiver marcado no admin; se não estiver, adia 1 semana e avisa;
- não publica duas vezes (guarda tudo em `estado.json`);
- se a VPS ficou parada e o horário passou há mais de 6 horas, empurra o post para o mesmo horário do dia seguinte em vez de publicar fora de hora;
- tenta de novo em erro temporário da API (até 4 vezes);
- renova o token de 60 dias toda semana;
- avisa no seu celular (opcional, pelo app ntfy) quando publica, adia ou falha.

## 1. Requisitos

- Conta do Instagram **profissional** (a @academy.soaone já é: Criador de conteúdo). Carrossel, imagem e reel funcionam em conta de criador; **story pela API só em conta Empresa**. Se quiser agendar stories pelo script, mude o tipo da conta para Empresa (Configurações > Tipo de conta e ferramentas).
- Docker na VPS (o mesmo do portal).

## 2. Criar o app na Meta (uma vez, uns 15 minutos)

1. Entre em developers.facebook.com com o seu Facebook > **Meus apps** > **Criar app**.
2. Caso de uso: **Gerenciar mensagens e conteúdo no Instagram** (API do Instagram). Nome: `soaone-academy-publicador`.
3. No painel do app, abra **Instagram** > **Configuração da API com login do Instagram**.
4. Em **Gerar tokens de acesso**, clique em **Adicionar conta**, entre com a @academy.soaone e aceite as permissões `instagram_business_basic` e `instagram_business_content_publish`.
5. Copie o **token** gerado (já é o de longa duração, 60 dias) e o **ID da conta** que aparece ao lado.

O app pode ficar em modo **Desenvolvimento**: ele só publica na sua própria conta, que foi adicionada como testadora ao gerar o token, e para isso a Meta não exige revisão do app. Se a tela pedir que você aceite um convite de testador, aceite em Instagram > Configurações > Apps e sites.

## 3. Instalar na VPS

Copie as pastas `instagram/` e `site/ig/` para a pasta do portal na VPS (a mesma do `docker-compose.yml`, que já traz o serviço `instagram`).

```bash
cd /caminho/do/soaone-academy-vps/instagram
cp .env.example .env
nano .env                      # cole IG_TOKEN e IG_USER_ID
chmod 600 .env

docker compose run --rm instagram python3 publicar.py conta     # deve mostrar username academy.soaone
docker compose run --rm instagram python3 publicar.py validar   # 0 problema(s) e "URL pública ok"
docker compose run --rm instagram python3 publicar.py listar

docker compose up -d instagram
docker compose logs -f instagram
```

Para testar sem publicar nada: `docker compose run --rm instagram python3 publicar.py rodar --simular`.

## 4. Uso no dia a dia

| Quero… | Comando ou ação |
|---|---|
| Ver o que está agendado e o que já saiu | `docker compose run --rm instagram python3 publicar.py listar` |
| Publicar um post agora | `… publicar.py publicar i2-modulo2` |
| Publicar mesmo com o módulo fechado | `… publicar.py publicar i2-modulo2 --forcar` |
| Mudar data ou legenda | editar `calendario.json` ou `legendas/*.txt`; vale na próxima verificação, sem reiniciar |
| Pausar um post | `"ativo": false` no calendário |
| Republicar um post que falhou | apagar a entrada dele em `estado.json` |
| Ver o log | `instagram/publicar.log` |
| Renovar o token na mão | `… publicar.py renovar-token` |

Depois de editar o calendário, rode `validar`: ele confere datas, tipos, arquivos, tamanho da legenda e se a primeira mídia abre pela URL pública.

## 5. Adicionar posts

Coloque as mídias em `site/ig/` (imagem **JPEG**, porque a API não aceita PNG; vídeo MP4) e acrescente um item em `calendario.json`:

```json
{"id": "reel-guardrails", "tipo": "reel", "quando": "2026-12-03T19:00:00-03:00",
 "midias": ["reel_guardrails.mp4"], "legenda": "13 guardrails… #LLM"}
```

- `tipo`: `imagem`, `carrossel` (2 a 10 mídias, na ordem), `reel` (1 vídeo vertical, `capa` opcional) ou `story`.
- `quando`: horário de Brasília. O post sai na primeira verificação depois dele, em até 5 minutos.
- `modulo` (opcional): só publica com o módulo liberado; senão adia `adiar_semanas` (padrão 1).
- `legenda_arquivo` (em `legendas/`) ou `legenda` direto no JSON. Máximo de 2.200 caracteres e 30 hashtags.

## 6. Calendário que já vem preenchido

| Post | Quando | Observação |
|---|---|---|
| i0-modulo0 | dom 22/11, 11h | **desligado** (`ativo: false`) para não duplicar com o Business Suite |
| i1-modulo1 | seg 23/11, 19h | **desligado**, pelo mesmo motivo |
| lancamento | ter 24/11, 19h | miniatura da apresentação + legenda de lançamento |
| i2 a i9 | quinta 19h da semana de cada liberação (10/12 a 28/01; 23/12 e 30/12 numa quarta por causa das festas) | cada um espera o seu módulo ser liberado |

Se você **não** agendou os carrosséis 0 e 1 no Business Suite, mude `ativo` deles para `true`. E se agendou o lançamento por lá, desligue o `lancamento` aqui.

## 7. Aviso no celular (opcional)

Instale o app **ntfy**, assine um tópico com nome difícil de adivinhar (ex.: `soaone-ig-7f3k2`) e coloque no `.env`:

```
NOTIFICAR_URL=https://ntfy.sh/soaone-ig-7f3k2
```

## 8. Testes

`python3 -m pytest -q` roda 11 testes com a API simulada (ordem do carrossel, módulo fechado, horário perdido, nova tentativa, token, reel e story, e se o calendário aponta para arquivos que existem).

## Segurança

- `.env` e `token.json` ficam só na VPS, com permissão 600, e estão no `.gitignore`.
- O container não tem rota no Traefik (`traefik.enable=false`): ele só faz chamadas de saída.
- O token só tem permissão para ler a conta e publicar conteúdo. Para revogar: Instagram > Configurações > Apps e sites > remover o app.
