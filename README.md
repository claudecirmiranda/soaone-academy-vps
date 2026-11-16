# soaone Academy — Fase 1 na VPS Hostinger

Site estático do curso gratuito **IA em Produção** + listmonk para captar leads do e-book.

## 1. DNS (no painel do seu domínio)
Crie dois registros **A** apontando para o IP da VPS:
- `academy.soaone.com.br`
- `listas.soaone.com.br`

## 2. Na VPS (Ubuntu com Docker)
```bash
# se ainda não tiver Docker:
curl -fsSL https://get.docker.com | sh

# copie esta pasta para a VPS (ex.: scp -r soaone-academy-vps usuario@IP:~)
cd ~/soaone-academy-vps
cp .env.example .env && nano .env      # troque as senhas
sudo ufw allow 80,443/tcp               # se usar firewall
docker compose up -d
```
O Caddy emite o certificado HTTPS sozinho em 1–2 minutos.

## 3. Conferir
- https://academy.soaone.com.br — landing + área do aluno
- https://listas.soaone.com.br — painel do listmonk (crie a lista "E-book IA em Produção" e configure o SMTP em Settings → SMTP: Brevo ou Amazon SES)

## 4. Integração com a API
O portal já vem ligado à API em `https://soaone.com.br/api` (pacote `soaone-api`, instalado na hospedagem do soaone.com.br). Instale a API antes, seguindo o README dela.
- O cadastro, o login e o progresso dos alunos ficam no MySQL da API.
- As aulas interativas **não** estão neste site: elas saem da API, só para alunos logados.
- O painel do administrador está em https://academy.soaone.com.br/admin.html.

Se a API mudar de endereço, troque a linha `window.SOAONE_API="..."` no `site/index.html` e no `site/admin.html`.

O listmonk continua opcional, para campanhas de e-mail. A lista de alunos que aceitaram receber e-mails sai do painel em **Exportar CSV** (coluna `aceita_emails`) e pode ser importada nele.

## 5. Atualizar o site
Envie o `site/index.html` novo para a VPS. O Caddy serve o arquivo novo na hora, sem reiniciar.

## Próximas fases
- **Fase 2:** certificado gratuito emitido pela API ao concluir as 34 aulas, e as aulas interativas que faltam.
- **Fase 3:** vitrine de projetos dos alunos, comunidade e tutor de IA com os guardrails do curso.

## Backup
```bash
docker compose exec listmonk-db pg_dump -U listmonk listmonk | gzip > backup-$(date +%F).sql.gz
```
Agende com `crontab -e` e copie para um object storage.
