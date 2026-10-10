@echo off
rem VialGuard streaming pipeline: Kafka (KRaft) + InfluxDB + stream processor, all running from D:\vialguard-infra.
rem Double-click, wait ~20 s, then stream data with:  python pipeline\producer.py --real
rem InfluxDB UI: http://localhost:8086  (login in D:\vialguard-infra\influx.env)
cd /d "%~dp0\.."
set "JAVA_HOME=%USERPROFILE%\.jdks\openjdk-26.0.2"
set "KAFKA_HEAP_OPTS=-Xmx512M -Xms256M"
start "VialGuard Kafka" /min cmd /c "cd /d D:\vialguard-infra\kafka && bin\windows\kafka-server-start.bat config\server.properties"
start "VialGuard InfluxDB" /min cmd /c "D:\vialguard-infra\influxdb\influxd.exe --bolt-path D:\vialguard-infra\influx-data\influxd.bolt --engine-path D:\vialguard-infra\influx-data\engine --http-bind-address 127.0.0.1:8086 --reporting-disabled"
echo Waiting for Kafka and InfluxDB to start...
timeout /t 20 /nobreak >nul
start "VialGuard stream processor" cmd /k "python pipeline\stream_processor.py"
start "" http://localhost:8086
echo Pipeline running. Now run:  python pipeline\producer.py --real
pause
