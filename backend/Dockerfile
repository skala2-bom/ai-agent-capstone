FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src src
RUN mvn -q package -DskipTests

FROM eclipse-temurin:21-jre
COPY --from=build /app/target/backend-0.0.1.jar /app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app.jar"]
