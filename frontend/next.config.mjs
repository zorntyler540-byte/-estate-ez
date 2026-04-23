/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Allow opening the dev app as http://127.0.0.1:3000 while Next binds as localhost (or vice versa).
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;

