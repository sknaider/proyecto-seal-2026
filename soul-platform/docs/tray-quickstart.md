# SOUL Tray — cablear el alma sin terminal

En Windows, el instalador abre un ícono violeta junto al reloj. Esa es la
interfaz de SOUL Platform para usuarios que no quieren manejar comandos.

Desde el menú podés:

- prender o apagar el alma sin borrar sus recuerdos;
- elegir cualquiera de tus modelos Ollama detectados automáticamente;
- copiar `http://127.0.0.1:11435/v1` para pegarlo como OpenAI API URL;
- copiar explícitamente el token local que la aplicación necesita;
- abrir la carpeta privada donde viven configuración y memoria.

## Flujo de conexión

1. Abrí el ícono **SOUL** junto al reloj.
2. Elegí un cerebro en **Elegir cerebro**.
3. Pulsá **Copiar endpoint para apps** y pegalo en el campo `Base URL` de tu app.
4. Pulsá **Copiar token local** y pegalo en el campo `API key` de esa misma app.
5. La aplicación hablará con el proxy local; SOUL inyectará identidad y memoria
   antes de delegar la respuesta al cerebro elegido.

El token no se muestra ni se registra: solo se copia por una acción explícita
del usuario. El proxy escucha en loopback, no en la red local.

Cerrar la interfaz de bandeja **no apaga el alma**. La opción **Prender / apagar
alma** controla el servicio persistente y conserva identidad, token y memoria.

## Diagnóstico

La comprobación no necesita entorno gráfico:

```text
soul-tray-cli --check
```

Devuelve JSON con estado, cerebro y modelos Ollama. Nunca incluye el token ni
contenido de memorias. Además compara UUID y baseline configurados: otro
servicio que ocupe el puerto no produce un falso verde. Devuelve código distinto
de cero si el alma no está lista. En Windows la bandeja queda registrada para
volver en cada inicio de sesión; el proxy y la interfaz tienen ciclos de vida
separados.
