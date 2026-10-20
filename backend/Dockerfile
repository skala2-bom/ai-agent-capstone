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
ENTRYPOINT ["java", "-jar", "/app.jar"]
