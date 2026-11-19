# Biblioteca de Modelos 3D

Servidor local para navegar por modelos 3D feitos no Blender. A página lista os
projetos, mostra prévias 3D ao vivo dos modelos e abre um visualizador
interativo com órbita, zoom e fechamento pelo **X** no canto inferior direito
(ou `Esc` / clique fora). Projetos e modelos podem ser criados, renomeados,
enviados e excluídos pela própria interface.

Não há etapa de build e não é necessário Node/npm. O backend usa apenas a
biblioteca padrão do Python; o frontend usa módulos ES e uma cópia local do
three.js em `static/vendor/three/`.

## Requisitos

- Python 3.10 ou mais recente.
- Um navegador com WebGL 2 (Chrome, Firefox, Edge etc.).
- Blender 4.2 ou mais recente **somente para converter arquivos `.blend` e
  `.fbx`**. Arquivos `.glb`/`.gltf` são servidos diretamente.

## Rodar

```bash
python3 server.py
```

Abra <http://127.0.0.1:8000>. Por padrão o servidor escuta em `0.0.0.0`,
então também aceita conexões da rede local. O log mostra os endereços de acesso,
por exemplo `http://192.168.17.237:8000`, que podem ser abertos no celular ou em
outro computador. Opções úteis:

```bash
python3 server.py --port 9000
python3 server.py --host 127.0.0.1
python3 server.py --blender "/caminho/para/blender"
```

Use `--host 127.0.0.1` para restringir ao próprio computador. Se outro
dispositivo não conseguir conectar, verifique o firewall da máquina e libere a
porta TCP correspondente (`8000` por padrão).

O servidor faz o scan de `projects/` a cada requisição. As operações de criar,
renomear, enviar e excluir feitas pela interface atualizam a lista sozinhas. Se
você alterar os arquivos por fora, recarregue a página com `Ctrl+R`; não é
preciso reiniciar o servidor nem gerar um índice.

## Estrutura

```text
projects/
  Xakricultura/
    models/
      argila 1/
        argila 1.blend
      copo_barro/
        copo_barro.blend
```

- Cada arquivo `.blend`, `.fbx`, `.glb` ou `.gltf` encontrado em `models/` vira
  um card. A estrutura em subpastas é a recomendada, mas arquivos soltos também
  são aceitos.
- O nome exibido é o nome do arquivo, sem a extensão. Se uma pasta tiver mais de
  um arquivo suportado, cada arquivo aparece separadamente.
- Extensões são reconhecidas sem diferenciar maiúsculas/minúsculas.
- Pastas e arquivos começando com `.` são ignorados.
- Projetos sem modelos aparecem com contagem `0`.

## Gerenciar pela interface

- **Novo projeto** cria `projects/<nome>/models/`.
- **Adicionar modelo** recebe um arquivo principal (`.blend`, `.fbx`, `.glb` ou
  `.gltf`) e, opcionalmente, arquivos auxiliares do `.gltf` (`.bin`, texturas)
  ou texturas externas do `.fbx`. O envio cria
  `models/<nome-do-arquivo>/` e grava tudo dentro dela.
- **Renomear modelo** renomeia o arquivo. Se ele estiver em uma pasta dedicada
  com o mesmo nome, a pasta também é renomeada.
- **Excluir modelo** apaga o arquivo. Quando a pasta dedicada fica sem modelos,
  ela também é removida, incluindo texturas auxiliares. Pastas compartilhadas
  por vários modelos são preservadas.
- **Renomear projeto** renomeia a pasta do projeto. **Excluir projeto** abre um
  formulário de confirmação e apaga a pasta inteira.

O envio aceita até 64 arquivos e 512 MB por requisição. Tudo é local e não há
autenticação. Como o padrão escuta em `0.0.0.0`, qualquer dispositivo que alcance
a porta pode criar e excluir conteúdo. Use apenas em uma rede confiável ou
restrinja com `--host 127.0.0.1`.

## Conversão de `.blend` e `.fbx`

O navegador não renderiza `.blend` nem `.fbx`. Na primeira abertura de um
desses modelos, o servidor executa o Blender em modo headless e gera um `.glb`
comprimido com Draco em `.cache/glb/`. Para `.fbx`, o Blender importa o arquivo
em uma cena vazia, carrega texturas embutidas e procura texturas externas na
pasta do modelo. Depois, a conversão:

- remove câmeras e luzes;
- remove malhas com nomes **exatos** `Plane`, `Ground`, `Chão` ou `Piso`
  (configurável em `config.json`);
- preserva a malha quando ela é o objeto de instância de um sistema de
  partículas: o objeto solto sai da cena, mas as instâncias continuam no GLB.
  É o caso das folhas de `Àrvore janipapo`, que usa uma malha chamada `Plane`
  como molde das partículas;
- aplica modificadores e exporta todas as outras malhas com materiais e
  texturas incorporados;
- usa cache por caminho, data de modificação, tamanho e assinatura dos
  exportadores. Para `.fbx`, os arquivos vizinhos também entram na assinatura,
  então trocar uma textura externa força uma nova conversão. Se o modelo mudar,
  ele é convertido de novo.

A primeira conversão pode levar alguns segundos; as seguintes usam o cache. As
conversões são serializadas para não abrir vários Blenders ao mesmo tempo. Para
forçar tudo a ser convertido novamente, pare o servidor e apague `.cache/`.

> O Blender do sistema (4.0.2) não consegue abrir os `.blend` comprimidos com
> Zstandard usados neste acervo e não tem NumPy para o exportador glTF. Por isso
> `config.json` aponta para o Blender 5.2.3 instalado em `~/Display`. Se esse
> caminho mudar, ajuste `blender_bin` ou defina `BLENDER_BIN`.

Configuração em `config.json`:

| Chave | Padrão | Função |
| --- | --- | --- |
| `host` | `0.0.0.0` | Endereço de escuta (todas as interfaces) |
| `port` | `8000` | Porta |
| `projects_dir` | `projects` | Pasta dos projetos |
| `static_dir` | `static` | Pasta dos arquivos do frontend |
| `blender_bin` | caminho local | Executável do Blender |
| `cache_dir` | `.cache` | Cache de GLBs convertidos |
| `exclude_mesh_names` | `Plane`, `Ground`, `Chão`, `Piso` | Malhas removidas na conversão |
| `conversion_timeout_seconds` | `300` | Limite por conversão |

`BLENDER_BIN` tem prioridade sobre `config.json`. `--blender` tem prioridade
sobre os dois.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

Teste único:

```bash
python3 -m unittest tests.test_library.LibraryTests.test_scan_projects_and_models -v
```

Os testes de conversão abrem o Blender de verdade e são pulados se ele não for
encontrado. Eles usam diretórios temporários e não alteram `projects/`. A suíte
também cobre o CRUD de projetos e modelos, a proteção contra path traversal, a
conversão de `.fbx` e a exportação de instâncias de partículas.

## API usada pelo frontend

- `GET /api/projects`: projetos, modelos e URLs de asset; refeito a cada
  requisição.
- `POST /api/projects`: cria um projeto. Corpo JSON: `{"name": "..."}`.
- `POST /api/projects/rename`: renomeia. Corpo:
  `{"project": "...", "name": "..."}`.
- `POST /api/projects/delete`: exclui. Corpo: `{"project": "..."}`.
- `POST /api/models/upload?project=<projeto>`: recebe
  `multipart/form-data` com um campo `files` contendo um modelo principal e,
  se for `.gltf` ou `.fbx`, os arquivos auxiliares e texturas externas.
- `POST /api/models/rename`: renomeia. Corpo:
  `{"project": "...", "path": "...", "name": "..."}`.
- `POST /api/models/delete`: exclui. Corpo:
  `{"project": "...", "path": "..."}`.
- `GET /assets/<projeto>/<caminho-no-models>`: serve `.glb`, `.gltf` e recursos
  relativos (`.bin`, texturas) com proteção contra path traversal.
- `GET /api/glb?project=<projeto>&path=<caminho>`: converte ou serve o GLB em
  cache de um `.blend` ou `.fbx`.
- `GET /api/health` e `GET /health`: diagnóstico do servidor.

As rotas de escrita usam JSON ou multipart e respondem com erro em JSON. Nomes
inválidos, caminhos com `..` e tentativas de sair de `projects/` são rejeitados
com HTTP 400; conflitos de nome respondem com HTTP 409.

As rotas da SPA usam hash:

- `#/`: lista de projetos;
- `#/p/<projeto>`: modelos do projeto;
- `#/p/<projeto>/m/<modelo>`: projeto com o visualizador aberto.

## Notas de implementação

- O viewer usa three.js r187 vendorizado, `OrbitControls`, `GLTFLoader`,
  `DRACOLoader` e `RoomEnvironment`. Não edite `static/vendor/three/` à mão;
  substitua o diretório inteiro se atualizar a versão (licença em
  `static/vendor/three/LICENSE`).
- Os cards renderizam prévias 3D reais com um único renderer WebGL
  compartilhado. Cada card tem um canvas 2D próprio e recebe a última imagem
  renderizada. As prévias começam quando o card fica 90% visível; ao sair da
  tela, a animação pausa, mas a cena e a imagem continuam na memória. Voltar a
  rolar não baixa nem reinterpreta o GLB. Ao abrir o dialog, as prévias pausam.
- O dialog usa um renderer WebGL separado e descarta geometria, materiais,
  texturas, renderer, controles e listeners ao fechar.
- O chão é filtrado por nome exato de propósito: há modelos legítimos com
  malhas chamadas `Plane.002` (ex.: `Croata - planta`), então não use correspondência
  por prefixo/sufixo. A exceção é apenas para objetos usados como molde de
  partículas, que são desvinculados da cena em vez de removidos.

## Solução de problemas

- **`Blender não encontrado`**: ajuste `blender_bin` em `config.json` ou exporte
  o modelo como `.glb`.
- **`.blend` não abre / erro de versão**: use Blender 4.2+; arquivos salvos em
  versões mais novas podem não abrir em versões antigas.
- **`.fbx` não abre**: confira o erro detalhado no dialog. Versões muito antigas
  do formato podem não ser aceitas pelo importador do Blender; reexporte o FBX
  em uma versão recente ou como `.glb`.
- **Modelo demora na primeira abertura**: é a conversão para GLB; acompanhe o
  progresso no card/dialog. As próximas aberturas usam `.cache/glb/`.
- **Porta ocupada**: rode com `--port 9000` ou altere `port` no `config.json`.
- **Upload recusado**: envie exatamente um `.blend`, `.fbx`, `.glb` ou `.gltf`
  por vez. Para `.gltf`, inclua também o `.bin` e as texturas referenciadas.
  Para `.fbx`, inclua as texturas externas que ele usa.
- **Prévia indisponível**: abra o card para ver o erro detalhado do Blender; se
  o modelo for só um plano de chão filtrado, remova o nome da lista
  `exclude_mesh_names`.
