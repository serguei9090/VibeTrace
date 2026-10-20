import { UserManager, type UserConfig } from "./models";

export class AuthService {
  private userManager: UserManager;

  constructor() {
    this.userManager = new UserManager("admin");
  }

  public register(userId: string): UserConfig {
    const config = this.userManager.createUser(userId);
    return config;
  }
}

export function handleRegistration(userId: string): UserConfig {
  const service = new AuthService();
  return service.register(userId);
}
