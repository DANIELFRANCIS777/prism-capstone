# Dev-server container (matches the project's "run it, don't productionize it"
# approach for the backend too) - fine for a capstone demo console.
FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
