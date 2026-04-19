#include <DHT.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <WebServer.h>

#define TEMPHUM 4        // GPIO pin connected to DATA
#define MOISTURE 5
#define SUNLIGHT 6
#define WATERLEVEL 7

#define MOTOR_A_IN1 18 // high = pump running
#define MOTOR_A_IN2 21
#define MOTOR_EN_A 17 // high enables the H-bridge channel

const char* ssid = "StarkHacks-2";
const char* password = "StarkHacks2026";

#define DHTTYPE DHT11   // DHT 11

DHT dht(TEMPHUM, DHTTYPE);

// WebSocket server (laptop / cloud / viam / node server)
const char* websocket_host = "10.10.11.234"; // change this
const uint16_t websocket_port = 8765;
const char* websocket_path = "/";

WebSocketsClient webSocket;

unsigned long lastSend = 0;

void setup() 
{
  //Serial initialization
  Serial.begin(115200);

  //Wifi initialization
  WiFi.begin(ssid, password);
  Serial.print("Connecting WiFi");
  Serial.println(WiFi.localIP());

  while (WiFi.status() != WL_CONNECTED) 
  {   
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi connected!");

  // WebSocket setup
  webSocket.begin(websocket_host, websocket_port, websocket_path);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(2000);

  //initialize temp sensor
  dht.begin();
}

//5 seconds per upstream transmission

void loop() 
{
  webSocket.loop();

  // send data every 2 seconds
  if (millis() - lastSend > 2000) 
  {
    lastSend = millis();

    delay(500);
    int sunlight = readSunlight();
    int moisture = readMoisture();
    int waterLevel = readWaterLevel();
    //int temperature = dht.readTemp();
    //int humidity = dht.readHumidity();

    String json = "{";
    json += "\"timestamp\":" + String(millis()) + ",";
    json += "\"light_level\":" + String(sunlight) + ",";
    json += "\"moisture\":" + String(moisture) + ",";
    json += "\"water_level\":" + String(waterLevel) + ",";
    //json += "\"temperature\":" + String(temperature) + ",";
    //json += "\"humidity\":" + String(humidity);
    json += "}";
    

    // String json = "{";
    // json += "\"timestamp\":";
    // json += String(millis());
    // json += "\"light_level\":";
    // json += String(sunlight);
    // json += "\"temperature_c\":";
    // json += String(temperature);
    // json += "\"humidity_percent\":";
    // json += String(humidity);
    // json += "\"water_level_percent\":";
    // json += String(waterLevel);
    // json += "\"light_level\":";
    // json += String(light_level);
    // json += "}";

    webSocket.sendTXT(json);

    Serial.println("Sent: " + json);
  }


  // Serial.println("Temp: " + readTemp());
  // Serial.println("Humidity: " + readHum());
  //Serial.printf("Light: %d\n ", readSunlight());
}

int readTemp() 
{
   return (int)dht.readTemperature(); //Celsius
}

int readHum()
{
   return (int)dht.readHumidity();
}

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_CONNECTED:
      Serial.println("WebSocket Connected!");
      break;

    case WStype_DISCONNECTED:
      Serial.println("WebSocket Disconnected!");
      break;

    case WStype_TEXT:
      Serial.printf("Received: %s\n", payload);
      break;
  }
}

int readMoisture() 
{
  return analogRead(MOISTURE);
}

int readSunlight() 
{
  return digitalRead(SUNLIGHT);
  //return analogRead(SUNLIGHT);
}

int readSunlightFiltered() {
  int sum = 0;

  for (int i = 0; i < 5; i++) {
    sum += analogRead(SUNLIGHT);
    delay(2);
  }

  return sum / 5;
}

int readWaterLevel() 
{
  return analogRead(WATERLEVEL);
}