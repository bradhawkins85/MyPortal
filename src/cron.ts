export function getRandomDailyCron(): string {
  const minute = Math.floor(Math.random() * 60);
  const hour = Math.floor(Math.random() * 24);
  return `${minute} ${hour} * * *`;
}
