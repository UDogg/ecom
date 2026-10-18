FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN ./mvnw -q -B dependency:go-offline
COPY src src
RUN ./mvnw -q -B package -DskipTests

FROM eclipse-temurin:21-jre-alpine
RUN addgroup -S app && adduser -S -G app -u 10001 app \
    && mkdir -p /var/lib/auth && chown app:app /var/lib/auth
COPY --from=build /src/target/auth-service-*.jar /app/auth-service.jar
USER app
# The signing key lives on a volume so restarts keep issued tokens valid.
ENV AUTH_SIGNING_KEY_PATH=/var/lib/auth/signing-key.json
VOLUME /var/lib/auth
EXPOSE 8081
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/auth-service.jar"]
