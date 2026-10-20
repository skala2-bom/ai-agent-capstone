# 빌드 단계가 없다. CDN Vue를 쓰는 정적 파일 한 장이라 그대로 얹기만 하면 된다.
# Vite로 전환하면 node 빌드 단계가 앞에 붙는다.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/
