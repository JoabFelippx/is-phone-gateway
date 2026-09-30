# Phone Gateway

Interface web para coletar sensores do celular e publicar mensagens protobuf no RabbitMQ do LabSEA. O gateway é Python (FastAPI + `is-wire-sea`); a página usa JavaScript para acessar as APIs de sensores do navegador.

O site foi escolhido para funcionar em Android e iPhone sem compilar ou instalar um aplicativo. **O Python roda em um computador na rede local, e é ele que se conecta ao broker.** O celular envia as leituras ao Python por WebSocket. Para publicação AMQP diretamente pelo celular e coleta contínua com a tela bloqueada, seria necessário um aplicativo nativo; esta versão coleta com a página visível.

```text
Celular / navegador ── HTTPS + WebSocket ── Gateway Python ── AMQP ── RabbitMQ
```

## Instalar

Use Python 3.10 ou superior, preferencialmente 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

As dependências `is-msgs-sea` e `is-wire-sea` são instaladas automaticamente e mantêm os imports `is_msgs` e `is_wire`. O gateway requer `is-msgs-sea` a partir da versão 1.3.1 e `is-wire-sea` a partir da versão 2.0.2.

## HTTPS na rede local

Câmera, localização e sensores de movimento exigem um contexto seguro. Abrir `http://192.168.x.x` no celular não atende a esse requisito. Use um certificado HTTPS cuja CA seja confiável pelo celular. [Documentação do acesso à câmera](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

Se já tiver certificado e chave válidos para o endereço do servidor, use-os. Para criar uma CA local e um certificado para seu IP, com OpenSSL instalado:

```bash
python scripts/create_certificate.py --ip 192.168.1.20
```

Substitua o IP pelo endereço do computador que executará o gateway. O script cria `certs/rootCA.pem`, `certs/gateway.pem` e as respectivas chaves privadas. Transfira **somente `rootCA.pem`** para o celular e instale como certificado de CA confiável nas configurações do sistema. O caminho varia conforme sistema e fabricante. No iPhone, instale o perfil do certificado e habilite confiança total para essa CA em Ajustes → Geral → Sobre → Ajustes de Confiança de Certificados. Não basta ignorar o aviso de certificado no navegador. Se o IP mudar, gere um novo certificado em outro diretório.

Mantenha os arquivos `*-key.pem` privados. O diretório `certs/` está no `.gitignore`. Remova a CA do celular quando deixar de usá-la. As instruções de confiança do iPhone estão na [documentação da Apple](https://support.apple.com/en-us/102390).

## Executar e publicar

```bash
source .venv/bin/activate
phone-gateway --cert certs/gateway.pem --key certs/gateway-key.pem
```

No celular, na mesma rede, abra `https://192.168.1.20:8443`. Libere a porta TCP 8443 no computador se houver firewall. O broker deve ser acessível pelo computador do gateway; não precisa habilitar plugins WebSocket do RabbitMQ.

1. O broker vem preenchido com `amqp://guest:guest@10.10.50.176:30000`; altere a URI se necessário. Informe o exchange (padrão `is`) e o nome do celular.
2. Ative os sensores desejados. Escolha um tópico distinto e uma frequência para cada um. Na câmera, a taxa é exibida em FPS e aceita até 30 FPS; escolha também frontal/traseira, resolução máxima da publicação e qualidade JPEG.
3. Toque em **Iniciar publicação** e conceda as permissões. A prévia e os contadores mostram a atividade real.
4. Toque em **Parar** para fechar a câmera, remover os listeners e encerrar as conexões. Ao sair da página ou colocá-la em segundo plano, a sessão também para.

O tópico de câmera padrão é `cameraphonegateway.frame`, e pode ser alterado para `cameraphonegatewat.frame` ou qualquer tópico válido. Tópicos aceitam letras ASCII, números, `_`, `-` e segmentos separados por `.`; são sensíveis a maiúsculas. `*` e `#` são curingas de assinatura, não tópicos de publicação.

Para configurar as credenciais no servidor em vez de digitá-las no celular:

```bash
export BROKER_URI='amqp://guest:guest@10.10.50.176:30000'
export GATEWAY_TOKEN='um-token-para-o-laboratorio'
phone-gateway --cert certs/gateway.pem --key certs/gateway-key.pem
```

Sem `BROKER_URI`, o servidor usa `amqp://guest:guest@10.10.50.176:30000` como padrão. Com `BROKER_URI`, o campo do broker pode ficar vazio e o servidor usa a URI do ambiente. Com `GATEWAY_TOKEN`, a interface exige esse token antes de conectar. As credenciais configuradas no ambiente não são enviadas pela API de configuração. A página salva tópicos, frequências e opções no navegador; não salva URI do broker nem token. Não há carregamento de scripts ou fontes de terceiros.

O RabbitMQ normalmente restringe o usuário `guest` ao próprio host. O broker em `10.10.50.176` precisa permitir explicitamente acesso remoto de `guest`; se não permitir, use um usuário autorizado no vhost e no exchange. Caracteres especiais no usuário/senha precisam de URL encoding. `amqps://` usa TLS com verificação de certificado pelo servidor Python.

## Calibrar a câmera

A calibração funciona sem iniciar a publicação no broker. Na seção **Calibração da câmera**, selecione a câmera frontal ou traseira, escolha a resolução máxima das fotos e toque em **Abrir câmera**. A página mostra a resolução que o aparelho realmente entregou. Posicione o tabuleiro em diferentes inclinações e distâncias; toque em **Capturar foto** de 3 a 5 vezes. Cada foto é aceita somente quando o servidor detecta pelo menos oito cantos ChArUco. É possível remover uma foto ruim e capturar outra.

O tabuleiro usa padrão **legacy**, 8 × 6 quadrados, lado do quadrado de 0,095 m, lado do marcador de 0,071 m e dicionário `DICT_4X4_100`. O link **Baixar tabuleiro** fornece o padrão gerado pelo próprio gateway. Ao imprimi-lo, confira que suas dimensões físicas sejam 760 × 570 mm; imprimir com ajuste automático à página muda o tamanho dos quadrados.

Ao tocar em **Calibrar e salvar NPZ**, o Python calcula os intrínsecos e salva `calibrations/<nome-do-celular>_<largura>x<altura>.npz` no computador do gateway, usando a resolução **real das fotos**, por exemplo `phone_1600x1200.npz`. O arquivo contém `K`, `dist`, `nK`, `roi`, `w`, `h`, erro RMS, número de fotos e os parâmetros do tabuleiro. Também pode ser baixado no celular. O diretório `calibrations/` não é enviado ao Git. Calibrações de resoluções diferentes coexistem; uma calibração da mesma resolução só é substituída quando a opção correspondente está marcada.

Por padrão, o arquivo também inclui `rt` (3 × 4), como os NPZ do pipeline de poses de referência. Esse `rt` descreve a pose da câmera **em relação ao tabuleiro visto na primeira foto válida**; só serve como extrínseco entre várias câmeras se o mesmo tabuleiro permanecer fixo no mesmo referencial durante suas calibrações. É possível desmarcar `rt` e salvar somente os parâmetros intrínsecos. Um `rt` obtido com tabuleiros movidos não representa um referencial comum entre câmeras.

As opções de resolução são as mesmas na calibração e na publicação, até 2560 × 1920. Ao salvar a calibração, a interface escolhe automaticamente a mesma câmera e a **resolução real** das fotos para publicação. **Automática (sem corte)** usa os pixels entregues pelo aparelho, respeitando o teto do gateway. As demais opções são limites máximos de largura e altura: preservam a proporção completa do quadro, portanto o navegador pode entregar uma resolução diferente da selecionada. A interface mostra a resolução de origem e a publicada e avisa se esta não corresponder à escolhida. O gateway solicita ao navegador `resizeMode: none` para evitar recorte digital, mas a disponibilidade e o campo de visão final dependem do navegador, da lente e do dispositivo. [Restrições de captura do navegador](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints). Os intrínsecos salvos correspondem à lente e à resolução **real das fotos de calibração**. Para análise geométrica, confirme que os frames publicados têm essa mesma resolução ou transforme `K` para um redimensionamento uniforme que preserve o campo de visão.

## Mensagens e unidades

| Sensor | Tipo protobuf do is-msgs-sea | Conteúdo |
| --- | --- | --- |
| Câmera | `is.vision.Image` | `data`: bytes JPEG; `header`: instante de recepção no gateway e `device_id/camera`; `sequence`: contador da sessão |
| Acelerômetro | `is.common.Vector3` | `x/y/z` em m/s², **sem gravidade** |
| Giroscópio | `is.common.Vector3` | `x/y/z` em rad/s; converte `beta/gamma/alpha` do navegador, que chegam em graus/s |
| Orientação | `is.ros.ROSMessage` | `type = phone_gateway/DeviceOrientation`; `content` com `alpha/beta/gamma` em radianos, `absolute`, `rotation_order` e `unit` |
| Localização | `is.ros.ROSMessage` | `type = phone_gateway/Geolocation`; `latitude/longitude` em graus, `accuracy_m`; campos opcionais `altitude_m`, `altitude_accuracy_m`, `heading_deg` e `speed_m_s` |
| Bateria | `is.common.PowerInfo` | `charge` de 0 a 1; `status`; `autonomy` quando conhecida |

Todas as mensagens têm metadados IS: `device_id`, `sensor`, `observed_at_ms` (horário Unix em ms do celular), `schema` e `sequence`. Os vetores também identificam suas unidades. O timestamp do celular depende do relógio do dispositivo; não representa sincronização de hardware entre sensores. `Image.timestamp_source` identifica o instante de recepção no gateway; o momento de captura aproximado do navegador fica nos metadados.

Os tipos dinâmicos reutilizam o schema oficial `ROSMessage`, mas seus nomes `phone_gateway/*` são tipos desta aplicação, não mensagens ROS padronizadas. A altitude do navegador usa nível do mar, então não é apresentada como `sensor_msgs/NavSatFix`, que usa o elipsoide WGS84. Valores ausentes não são preenchidos artificialmente. `PowerInfo` não informa tensão, capacidade ou química da bateria; valores protobuf padrão nesses campos significam informação ausente nesta aplicação.

Referências das unidades: [giroscópio do navegador](https://developer.mozilla.org/en-US/docs/Web/API/DeviceMotionEvent/rotationRate), [orientação](https://www.w3.org/TR/orientation-event/) e [altitude](https://developer.mozilla.org/en-US/docs/Web/API/GeolocationCoordinates/altitude).

## Disponibilidade e entrega

O suporte depende do aparelho, navegador e permissões. A [API de bateria](https://developer.mozilla.org/en-US/docs/Web/API/Battery_Status_API) tem disponibilidade limitada, geralmente em Chromium/Android e não em Safari/iPhone. Movimento e orientação podem exigir autorização explícita pelo botão de início no iOS. Acelerômetro sem gravidade, giroscópio ou GPS podem não fornecer leituras; a interface informa isso, sem enviar valores fictícios. Localização é a estimativa do navegador e não garante uma leitura de GPS físico.

A frequência configura um teto de publicação, não a frequência física do sensor. Na câmera, a interface usa FPS (frames por segundo), com padrão de 2 FPS e limite de 30 FPS. A taxa efetiva depende da câmera, da codificação JPEG, da rede e do tempo de publicação. Localização envia quando há uma nova leitura. Há no máximo uma mensagem aguardando retorno por sensor, para evitar acúmulo. Imagens têm teto de 6 MiB por quadro, são redimensionadas no navegador e são publicadas por `publish_stream` com TTL de dois segundos. Telemetria usa `publish` e um canal separado. Cada celular tem configuração, contadores e conexões independentes; tópicos iguais entre celulares misturam as leituras, identificadas por `device_id`.

Os contadores indicam chamadas de publicação concluídas, **não confirmação de processamento pelo consumidor**. A entrega segue a semântica do `is-wire-sea`: no máximo uma vez, sem persistência ou repetição automática. Em falha de conexão, a sessão para e pode ser reiniciada pelo usuário. Consumidores precisam assinar os tópicos antes de iniciar para receber os dados.

## Consumir e verificar

Exemplo com a câmera:

```bash
export BROKER_URI='amqp://guest:guest@10.10.50.176:30000'
python examples/consume.py --sensor camera --topic cameraphonegateway.frame
```

O consumidor grava o último JPEG em `frame.jpg`. Para sensores:

```bash
python examples/consume.py --sensor gyroscope --topic phonegateway.angular_velocity
```

Verificação local:

```bash
python -m pytest
ruff check phone_gateway tests scripts examples
```

Os testes verificam serialização protobuf, conversões, validação, roteamento, isolamento entre celulares, autenticação e fechamento dos canais, com publicadores simulados. Para verificar a integração real, use o consumidor com seu RabbitMQ e o celular. Testar acesso físico aos sensores exige um aparelho real.

O teste opcional da interface usa câmera, sensores e respostas de publicação simulados no navegador. Ele verifica desktop/celular, início/parada, reinício e ausência de credenciais no armazenamento local:

```bash
python -m pip install -e '.[browser]'
python scripts/check_browser.py
```

Se não houver Google Chrome instalado, execute `python -m playwright install chromium` antes. É possível indicar o binário com `--browser /caminho/do/chrome`. O teste salva capturas de tela em `/tmp/phone-gateway-desktop.png` e `/tmp/phone-gateway-mobile.png`.

Para desenvolver apenas a interface no próprio computador, `phone-gateway --host 127.0.0.1 --port 8000` permite abrir `http://localhost:8000`; `localhost` pode ser considerado seguro pelo navegador. Esse endereço não funciona como contexto seguro quando substituído pelo IP local no celular.
