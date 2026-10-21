# 1단계: 빌드. pom.xml을 먼저 복사해 의존성을 받아 두면, 소스만 고쳤을 때
#        이 레이어가 캐시돼서 다시 빌드할 때 훨씬 빠르다.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src src
RUN mvn -q package -DskipTests

# 2단계: 실행. JDK 대신 JRE만 담아 이미지를 줄인다.
FROM eclipse-temurin:21-jre
COPY --from=build /app/target/backend-0.0.1.jar /app.jar
EXPOSE 8080
# DB·RabbitMQ까지 붙어야 UP이다 (Boot가 자동으로 본다). Spring이 뜨는 데 10~20초 걸려서 start-period를 넉넉히 둔다
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=5 \
    CMD curl -fs http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["java", "-jar", "/app.jar"]
