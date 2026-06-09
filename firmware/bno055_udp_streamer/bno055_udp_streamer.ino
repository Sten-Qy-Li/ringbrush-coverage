#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO055.h>
#include <M5StickCPlus.h>

// ==== Mobile Tethering and/or Hotspot settings ====
// Change these for your mobile hotspot (or Wi-Fi network)
const char* WIFI_SSID = "Insert your Hotspot SSID here";
const char* WIFI_PASS = "Insert your Hotspot password here";

// Target laptop IP address
// Example: 192.168.0.207
IPAddress LAPTOP_IP(192, 168, 0, 207);

// UDP target port
const uint16_t UDP_PORT = 5005;

// Match the M5Stick external pins
const int SDA_PIN = 0;   // G0 → SDA
const int SCL_PIN = 26;  // G26 → SCL

// BNO055 at address 0x29
Adafruit_BNO055 bno = Adafruit_BNO055(55, BNO055_ADDRESS_B);

// ==== UDP object ====
WiFiUDP udp;

unsigned long lastBatUpdate = 0;

void connectWiFi() {
  Serial.print("Connecting to Mobile Hotspot: ");
  M5.Lcd.print("Connecting to Mobile Hotspot: ");
  Serial.println(WIFI_SSID);
  M5.Lcd.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int retry = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    retry++;
    if (retry > 60) { // Restart if not connected after about 30 seconds
      Serial.println("\nFailed to connect to Mobile Hotspot, restarting...");
      ESP.restart();
    }
  }

  Serial.println("\nMobile Hotspot connected.");
  M5.Lcd.println("\nMobile Hotspot connected.");
  Serial.println(WiFi.localIP());
}

void setup() {
  M5.begin();              // 電源・LCDなど初期化（NTPコードと同じ）
  Serial.begin(115200);
  delay(200);

  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setRotation(1);
  M5.Lcd.setCursor(0, 10);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setTextSize(2);

  Serial.println("=== BNO055 UDP STREAMER START ===");

  // I2C
  Wire.begin(SDA_PIN, SCL_PIN);
  Serial.println("Wire.begin done");

  if (!bno.begin()) {
    Serial.println("BNO055 not detected at 0x29. Check wiring.");
    M5.Lcd.println("BNO055 not detected at 0x29. Check wiring.");
    while (1) {
      delay(100);
    }
  }
  Serial.println("BNO055 init OK");

  delay(1000);
  bno.setExtCrystalUse(true);
  Serial.println("Using external crystal");

  // Connect to the mobile hotspot
  connectWiFi();

  // Binding a local UDP port is optional for sending only,
  // but we set one here anyway.
  udp.begin(UDP_PORT);

  Serial.println("t_ms,roll,pitch,yaw,ax,ay,az");

  delay(2000);
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setCursor(0, 10);
}

void loop() {
  sensors_event_t orientationData, accelData;

  bno.getEvent(&orientationData, Adafruit_BNO055::VECTOR_EULER);
  bno.getEvent(&accelData,       Adafruit_BNO055::VECTOR_ACCELEROMETER);

  unsigned long t = millis();

  // Build one CSV line as a string
  String line;
  line.reserve(80); // Rough buffer size
  line += String(t);
  line += ",";
  line += String(orientationData.orientation.x, 2);
  line += ",";
  line += String(orientationData.orientation.y, 2);
  line += ",";
  line += String(orientationData.orientation.z, 2);
  line += ",";
  line += String(accelData.acceleration.x, 3);
  line += ",";
  line += String(accelData.acceleration.y, 3);
  line += ",";
  line += String(accelData.acceleration.z, 3);

  // Also print to serial for debugging
  Serial.println(line);

  unsigned long now = millis();
  if (now - lastBatUpdate >= 1000) {
    lastBatUpdate = now;
    float vbatt = M5.Axp.GetBatVoltage();
    float vbus  = M5.Axp.GetVBusVoltage();
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setCursor(0, 10);
    M5.Lcd.printf("Bat: %.3f V, \nUSB: %.3f V\n", vbatt, vbus);
  }

  // ==== UDP send ====
  udp.beginPacket(LAPTOP_IP, UDP_PORT);
  udp.print(line);
  udp.print("\n");  // Line break
  udp.endPacket();

  // 100 Hz
  delay(10);
}
